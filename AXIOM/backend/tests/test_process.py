"""Real loopback Uvicorn process, not just ASGI in-process testing."""

import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
AUTH = {"Authorization": "Bearer synthetic-dani-bakery"}


def test_process_start_openapi_and_persistence_after_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("DARLA_SYNTHETIC_MODE", "1")
    monkeypatch.setenv("DARLA_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    task_id = None
    for generation in range(2):
        with (tmp_path / f"uvicorn-{generation}.txt").open("w") as output:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "uvicorn",
                    "backend.app.main:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                    "--no-access-log",
                ],
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                with httpx.Client(base_url=base, trust_env=False, timeout=2) as client:
                    deadline = time.monotonic() + 30
                    while True:
                        try:
                            if client.get("/health/live").status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        assert process.poll() is None, (
                            tmp_path / f"uvicorn-{generation}.txt"
                        ).read_text()
                        assert time.monotonic() < deadline, "Backend did not become live"
                        time.sleep(0.1)
                    assert (
                        client.get("/openapi.json", timeout=30).json()["info"]["version"] == "0.1.0"
                    )
                    assert client.get("/health/ready", headers=AUTH).json()["application_ready"]
                    if generation == 0:
                        response = client.post(
                            "/api/v1/tasks",
                            headers={**AUTH, "Idempotency-Key": "process-task"},
                            json={"title": "Synthetic process restart proof"},
                        )
                        assert response.status_code == 201
                        task_id = response.json()["id"]
                    else:
                        response = client.get("/api/v1/tasks/" + task_id, headers=AUTH)
                        assert response.status_code == 200
                        assert response.json()["title"] == "Synthetic process restart proof"
            finally:
                process.terminate()
                process.wait(timeout=15)
