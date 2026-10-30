from datetime import UTC, datetime, timedelta

from test_backend import AUTH

AGENT = {"Authorization": "Bearer synthetic-agent-bakery", "Idempotency-Key": "runtime-test"}


def proposal(request_id="http-conformance-001"):
    expires = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    return {
        "input": {
            "principal_id": "synthetic-dani",
            "agent_id": "synthetic-darla-agent",
            "request_id": request_id,
            "goal": "Create one synthetic task",
            "domain": "bakery",
            "tool_allowlist": ["task.create"],
            "authorization": {
                "principal_id": "synthetic-dani",
                "session_id": "synthetic-agent-bakery",
                "domain": "bakery",
                "permitted_scopes": ["read", "runtime.propose", "run.write", "task.write"],
                "expires_at": expires,
            },
            "budget": {"max_tokens": 100, "max_tool_calls": 1, "max_cost_usd": "0"},
            "deadline": expires,
        },
        "result": {
            "request_id": request_id,
            "actor_id": "synthetic-darla-agent",
            "test_double": True,
            "action": {
                "requested_tool": "task.create",
                "parameters": {"title": "Synthetic HTTP runtime task"},
                "summary": "Propose a bounded synthetic task",
            },
        },
    }


def test_runtime_proposal_is_http_accessible_scoped_and_replay_safe(client):
    body = proposal()
    first = client.post("/api/v1/runtime/proposals", headers=AGENT, json=body)
    assert first.status_code == 201
    run = first.json()
    assert run["status"] == "QUEUED"
    assert (
        client.post(
            "/api/v1/runtime/proposals",
            headers={**AGENT, "Idempotency-Key": "another-key"},
            json=body,
        ).json()
        == run
    )
    body["result"]["action"]["parameters"]["title"] = "Changed task"
    assert client.post("/api/v1/runtime/proposals", headers=AGENT, json=body).status_code == 409
    assert client.get("/api/v1/tasks", headers=AUTH).json()["items"] == []
    assert client.get("/api/v1/agent/status", headers=AUTH).json()["status"] == "UNAVAILABLE"


def test_runtime_proposal_rejects_wrong_session_identity_scope_and_bypass(client):
    assert (
        client.post("/api/v1/runtime/proposals", headers=AUTH, json=proposal()).status_code == 403
    )
    assert client.post("/api/v1/runtime/proposals", json=proposal()).status_code == 401
    for field in ("session", "actor", "scope", "tool"):
        body = proposal("attack-" + field)
        if field == "session":
            body["input"]["authorization"]["session_id"] = "forged-session"
        elif field == "actor":
            body["input"]["agent_id"] = body["result"]["actor_id"] = "LDR"
        elif field == "scope":
            body["input"]["authorization"]["permitted_scopes"].append("approval.decide")
        else:
            body["result"]["action"]["requested_tool"] = "customer.send"
        assert client.post("/api/v1/runtime/proposals", headers=AGENT, json=body).status_code == 403
    assert (
        client.post(
            "/api/v1/runs",
            headers=AGENT,
            json={"tool": "task.create", "arguments": {"title": "bypass"}},
        ).status_code
        == 403
    )
    assert client.post("/api/v1/tasks", headers=AGENT, json={"title": "bypass"}).status_code == 403
    assert (
        client.post(
            "/api/v1/runtime/proposals",
            headers={"Authorization": "Bearer synthetic-agent-bakery"},
            json=proposal(),
        ).status_code
        == 400
    )


def test_runtime_checkpoint_cannot_claim_completion_or_change_authority(client, app):
    from backend.app.worker import Worker

    body = proposal("checkpoint-request")
    run = client.post("/api/v1/runtime/proposals", headers=AGENT, json=body).json()
    event = {
        "run_id": run["id"],
        "request_id": "checkpoint-request",
        "event_id": "checkpoint-001",
        "kind": "CHECKPOINT",
        "checkpoint_reference": "synthetic-checkpoint-v1",
    }
    result = client.post("/api/v1/runtime/events", headers=AGENT, json=event)
    assert result.status_code == 200 and result.json()["status"] == "QUEUED"
    assert result.json()["checkpoint"]["runtime_reference"] == "synthetic-checkpoint-v1"
    assert client.post("/api/v1/runtime/events", headers=AGENT, json=event).json() == result.json()
    forged = {**event, "kind": "COMPLETED"}
    assert client.post("/api/v1/runtime/events", headers=AGENT, json=forged).status_code == 422
    forged = {**event, "request_id": "another-request", "event_id": "forged-checkpoint"}
    assert client.post("/api/v1/runtime/events", headers=AGENT, json=forged).status_code == 403
    assert Worker(app.state.services).tick() == 1
    later = {**event, "event_id": "late-checkpoint"}
    assert client.post("/api/v1/runtime/events", headers=AGENT, json=later).status_code == 409


def test_runtime_reported_error_blocks_run_without_tool_effect(client):
    run = client.post(
        "/api/v1/runtime/proposals", headers=AGENT, json=proposal("error-request")
    ).json()
    event = {
        "run_id": run["id"],
        "request_id": "error-request",
        "event_id": "error-001",
        "kind": "ERROR",
        "error_classification": "UNAVAILABLE",
    }
    result = client.post("/api/v1/runtime/events", headers=AGENT, json=event)
    assert result.status_code == 200 and result.json()["status"] == "BLOCKED"
    assert client.get("/api/v1/tasks", headers=AUTH).json()["items"] == []
