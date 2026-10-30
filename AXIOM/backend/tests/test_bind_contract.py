from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_backend import AUTH
from test_runtime_http import AGENT, proposal

from backend.app.config import Settings
from backend.app.main import create_app


def test_default_bind_is_loopback_only(settings, monkeypatch):
    monkeypatch.delenv("DARLA_BIND_HOST", raising=False)
    monkeypatch.delenv("DARLA_ALLOW_DOCKER_HOST", raising=False)
    assert Settings.from_environment().bind_host == "127.0.0.1"
    assert settings.bind_host == "127.0.0.1"
    assert settings.bind_port == 8765
    assert settings.allow_docker_host is False


def test_server_entrypoint_passes_default_loopback_without_opening_socket(settings, monkeypatch):
    from backend.app import serve

    calls = []
    monkeypatch.setattr(serve.Settings, "from_environment", lambda: settings)
    monkeypatch.setattr(serve.uvicorn, "run", lambda app, **options: calls.append((app, options)))
    serve.main()
    assert len(calls) == 1
    assert calls[0][1]["host"] == "127.0.0.1"
    assert calls[0][1]["port"] == 8765
    assert calls[0][1]["proxy_headers"] is False


@pytest.mark.parametrize(
    "address",
    [
        "0.0.0.0",
        "::",
        "::1",
        "8.8.8.8",
        "localhost",
        "host.docker.internal",
        "169.254.1.1",
        "224.0.0.1",
    ],
)
def test_wildcard_public_hostname_and_unapproved_address_classes_fail_closed(settings, address):
    with pytest.raises(ValueError):
        replace(settings, bind_host=address)


def test_private_bind_is_explicit_synthetic_only_and_does_not_resolve_dns(settings, monkeypatch):
    configured = replace(settings, bind_host="172.30.96.1")
    assert configured.bind_host == "172.30.96.1"
    with pytest.raises(ValueError):
        replace(settings, synthetic_mode=False, bind_host="172.30.96.1")
    with pytest.raises(ValueError):
        replace(settings, synthetic_mode=False, allow_docker_host=True)
    monkeypatch.setenv("DARLA_BIND_HOST", "172.30.96.1")
    monkeypatch.setenv("DARLA_ALLOW_DOCKER_HOST", "1")
    monkeypatch.setenv("DARLA_SYNTHETIC_MODE", "1")
    from_env = Settings.from_environment()
    assert from_env.bind_host == "172.30.96.1" and from_env.allow_docker_host is True


def test_docker_host_rejected_by_default_and_explicitly_allowed_without_auth_bypass(settings):
    with TestClient(create_app(settings), base_url="http://host.docker.internal:8765") as default:
        assert default.get("/api/v1/agent/status", headers=AGENT).status_code == 400
    enabled = replace(settings, allow_docker_host=True)
    with TestClient(create_app(enabled), base_url="http://host.docker.internal:8765") as client:
        assert client.get("/api/v1/agent/status").status_code == 401
        assert client.get("/api/v1/agent/status", headers=AGENT).status_code == 200
        for host in ["evil.invalid", "host.docker.internal.evil.invalid"]:
            assert client.get("/health/live", headers={"Host": host}).status_code == 400
        assert (
            client.get(
                "/health/live",
                headers={"Host": "evil.invalid", "X-Forwarded-Host": "host.docker.internal"},
            ).status_code
            == 400
        )


def test_cors_remains_disabled_for_browser_cross_origin_requests(settings):
    enabled = replace(settings, allow_docker_host=True)
    with TestClient(create_app(enabled), base_url="http://host.docker.internal:8765") as client:
        result = client.get(
            "/api/v1/agent/status", headers={**AGENT, "Origin": "https://evil.invalid"}
        )
        assert result.status_code == 200
        assert "Access-Control-Allow-Origin" not in result.headers
        assert "Access-Control-Allow-Credentials" not in result.headers
        preflight = client.options(
            "/api/v1/runtime/proposals",
            headers={
                "Origin": "https://evil.invalid",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type,idempotency-key",
            },
        )
        assert preflight.status_code == 405
        assert "Access-Control-Allow-Origin" not in preflight.headers


def test_two_independent_application_clients_replay_one_runtime_proposal(settings):
    enabled = replace(settings, allow_docker_host=True)
    first_app = create_app(enabled)
    second_app = create_app(enabled)
    body = proposal("two-client-bind-conformance")
    with (
        TestClient(first_app, base_url="http://host.docker.internal:8765") as first,
        TestClient(second_app, base_url="http://host.docker.internal:8765") as second,
    ):

        def send(client):
            return client.post("/api/v1/runtime/proposals", headers=AGENT, json=body)

        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(send, [first, second]))
        assert [result.status_code for result in results] == [201, 201]
        assert results[0].json() == results[1].json()
        assert first.get("/api/v1/runs", headers=AUTH).json()["total"] == 1
        receipts = second.get("/api/v1/receipts", headers=AUTH).json()["items"]
        assert len(receipts) == 1 and receipts[0]["action"] == "run.create"
