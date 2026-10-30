from datetime import UTC, datetime, timedelta

import pytest
from test_backend import AUTH, post

from backend.app.worker import Worker


def test_partial_tool_failure_rolls_back_and_retries_after_restart(
    client, app, settings, monkeypatch
):
    services = app.state.services
    run = post(
        client, "/runs", {"tool": "task.create", "arguments": {"title": "Synthetic retry task"}}
    ).json()
    original = services._apply

    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("synthetic failure after insert")

    monkeypatch.setattr(services, "_apply", interrupted)
    assert Worker(services).tick() == 0
    assert client.get("/api/v1/tasks", headers=AUTH).json()["items"] == []
    progress = client.get("/api/v1/runs/" + run["id"], headers=AUTH).json()
    assert progress["status"] == "RETRY_PENDING"
    assert not any(
        r["action"] == "run.execute"
        for r in client.get("/api/v1/receipts", headers=AUTH).json()["items"]
    )
    from backend.app.main import create_app

    restarted = create_app(settings).state.services
    at = datetime.now(UTC) + timedelta(seconds=10)
    assert Worker(restarted).tick(at=at) == 1
    assert Worker(restarted).tick(at=at) == 0
    assert len(client.get("/api/v1/tasks", headers=AUTH).json()["items"]) == 1


def test_exhausted_retry_has_failed_receipt(client, app):
    services = app.state.services
    run = post(
        client, "/runs", {"tool": "task.create", "arguments": {"title": "Synthetic failed task"}}
    ).json()
    with services.store.transaction() as connection:
        services._save(connection, {**run, "status": "RETRY_PENDING", "attempts": 3})
    assert Worker(services).tick() == 0
    stored = client.get("/api/v1/runs/" + run["id"], headers=AUTH).json()
    assert stored["status"] == "FAILED"
    receipts = client.get("/api/v1/receipts", headers=AUTH).json()["items"]
    failure = [r for r in receipts if r["action"] == "run.execute"]
    assert len(failure) == 1 and failure[0]["outcome"] == "FAILED"
    assert client.get("/api/v1/tasks", headers=AUTH).json()["items"] == []


def test_expired_running_lease_recovers(client, app):
    run = post(
        client, "/runs", {"tool": "task.create", "arguments": {"title": "Synthetic lease recovery"}}
    ).json()
    with app.state.services.store.transaction() as connection:
        app.state.services._save(
            connection,
            {
                **run,
                "status": "RUNNING",
                "lease_until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
            },
        )
    assert Worker(app.state.services).tick() == 1
    assert client.get("/api/v1/runs/" + run["id"], headers=AUTH).json()["status"] == "COMPLETED"


def test_expired_approval_and_agent_identity_cannot_decide(client, app):
    services = app.state.services
    approval = services.seed_synthetic_approval(
        "synthetic-dani", "bakery", "Synthetic expired approval"
    )
    with services.store.transaction() as connection:
        services._save(
            connection,
            {**approval, "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()},
        )
    path = "/approvals/" + approval["id"] + "/decision"
    assert (
        post(
            client,
            path,
            {"decision": "APPROVED"},
            headers={"Authorization": "Bearer synthetic-agent-bakery"},
        ).status_code
        == 403
    )
    assert post(client, path, {"decision": "APPROVED"}).status_code == 409


def test_other_principal_and_read_only_session_are_isolated(client):
    task = post(client, "/tasks", {"title": "Synthetic private task"}).json()
    reader = {"Authorization": "Bearer synthetic-reader-bakery"}
    assert client.get("/api/v1/tasks/" + task["id"], headers=reader).status_code == 404
    assert post(client, "/tasks", {"title": "Denied task"}, headers=reader).status_code == 403
    assert client.get("/api/v1/tasks?limit=0", headers=AUTH).status_code == 422


def test_memory_replay_does_not_retain_corrected_or_deleted_values(client, app):
    from test_backend import PERSONAL

    memory = app.state.services.seed_synthetic_memory(
        "synthetic-dani", "personal", "Original synthetic preference", "fixture"
    )
    path = "/api/v1/memory/" + memory["id"]
    headers = {**PERSONAL, "Idempotency-Key": "correct-once"}
    body = {"value": "Corrected synthetic preference"}
    first = client.patch(path, headers=headers, json=body)
    assert client.patch(path, headers=headers, json=body).json() == first.json()
    deleted = client.delete(path, headers={**PERSONAL, "Idempotency-Key": "delete-once"})
    assert deleted.status_code == 200
    assert (
        client.delete(path, headers={**PERSONAL, "Idempotency-Key": "delete-once"}).json()
        == deleted.json()
    )
    assert client.get("/api/v1/memory", headers=PERSONAL).json()["items"] == []


def test_journal_tamper_is_detected_without_overwrite(client, app):
    from backend.app.security import Fault

    post(client, "/tasks", {"title": "Synthetic receipt integrity"})
    app.state.services.flush_journal()
    path = next((app.state.services.settings.state_dir / "receipts").glob("*.json"))
    path.write_text("synthetic corruption")
    with pytest.raises(Fault, match="differs"):
        app.state.services.flush_journal()
    assert path.read_text() == "synthetic corruption"
