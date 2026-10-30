from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from test_runtime_http import AGENT, proposal

from backend.app.main import create_app


@pytest.mark.parametrize("hostname", ["host.docker.internal", "host.openshell.internal"])
def test_proxy_aliases_remain_rejected_without_opt_in(settings, hostname):
    assert settings.bind_host == "127.0.0.1"
    with TestClient(create_app(settings), base_url=f"http://{hostname}:8765") as client:
        assert client.get("/health/live").status_code == 400


@pytest.mark.parametrize("hostname", ["host.docker.internal", "host.openshell.internal"])
def test_proxy_alias_opt_in_accepts_host_but_preserves_bearer_auth(settings, hostname):
    enabled = replace(settings, allow_docker_host=True)
    assert enabled.bind_host == "127.0.0.1"
    with TestClient(create_app(enabled), base_url=f"http://{hostname}:8765") as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/api/v1/agent/status").status_code == 401
        assert (
            client.get(
                "/api/v1/agent/status", headers={"Authorization": "Bearer invalid"}
            ).status_code
            == 401
        )
        assert client.get("/api/v1/agent/status", headers=AGENT).status_code == 200
        assert client.get("/api/v1/agent/status", headers=AGENT).json()["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("hostname", ["host.openshell.internal.evil.invalid", "unlisted.invalid"])
def test_opt_in_does_not_allow_unlisted_or_forwarded_hosts(settings, hostname):
    with TestClient(create_app(replace(settings, allow_docker_host=True))) as client:
        assert (
            client.get(
                "/health/live",
                headers={"Host": hostname, "X-Forwarded-Host": "host.openshell.internal"},
            ).status_code
            == 400
        )


def test_runtime_proposal_through_openshell_host_is_persisted_and_replay_safe(settings):
    enabled = replace(settings, allow_docker_host=True)
    with TestClient(create_app(enabled), base_url="http://host.openshell.internal:8765") as client:
        body = proposal("openshell-host-conformance")
        response = client.post("/api/v1/runtime/proposals", headers=AGENT, json=body)
        assert response.status_code == 201
        assert response.json()["status"] == "QUEUED"
        assert (
            client.post("/api/v1/runtime/proposals", headers=AGENT, json=body).json()
            == response.json()
        )
        assert client.get("/api/v1/runs", headers=AGENT).json()["total"] == 1
