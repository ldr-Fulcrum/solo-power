import csv
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openapi_spec_validator import validate

from backend.app.config import Settings
from backend.app.main import create_app
from backend.app.worker import Worker

ROOT = Path(__file__).resolve().parents[2]
AUTH = {"Authorization": "Bearer synthetic-dani-bakery"}
PERSONAL = {"Authorization": "Bearer synthetic-dani-personal"}


def post(client, path, payload, key="test-key", headers=None):
    return client.post(
        "/api/v1" + path, json=payload, headers={**(headers or AUTH), "Idempotency-Key": key}
    )


def test_health_readiness_and_provenance(client):
    assert client.get("/health/live").json()["status"] == "alive"
    ready = client.get("/health/ready", headers=AUTH)
    assert ready.status_code == 200
    assert ready.json()["application_ready"] is True
    assert ready.json()["autonomy_ready"] is False
    status = client.get("/api/v1/agent/status", headers=AUTH).json()
    assert status["status"] == "UNAVAILABLE"
    integrations = client.get("/api/v1/integrations", headers=AUTH).json()
    assert integrations["lightning"] == "PRIOR_TEST_CONFIRMED"
    assert integrations["super"] == "NOT_YET_VERIFIED"
    assert integrations["darla_runtime"] == "NOT_CONNECTED"


def test_openapi_matches_all_32_supplied_routes(client):
    schema = client.get("/openapi.json").json()
    validate(schema)
    rows = list(csv.DictReader((ROOT / "contracts/BACKEND_ENDPOINT_MATRIX.csv").open()))
    assert len(rows) == 32
    for row in rows:
        operation = schema["paths"][row["route"]][row["method"].lower()]
        assert operation.get("responses")
        if row["route"] != "/health/live":
            assert operation.get("security")
    assert schema["info"]["version"] == "0.1.0"


def test_auth_validation_errors_and_scopes(client):
    for route in ["/api/v1/tasks", "/health/ready", "/api/v1/memory"]:
        assert client.get(route).status_code == 401
    assert client.get("/api/v1/tasks", headers={"X-Principal": "Dani"}).status_code == 401
    bad = post(client, "/tasks", {"title": "", "unknown": True})
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "VALIDATION_ERROR"
    assert bad.headers["X-Correlation-ID"] == bad.json()["error"]["correlation_id"]
    assert (
        post(
            client, "/bakery/plans", {"title": "test", "minutes_required": 60}, headers=PERSONAL
        ).status_code
        == 403
    )
    assert client.post("/api/v1/tasks", headers=AUTH, json={"title": "test"}).status_code == 400


def test_task_restart_transitions_and_receipts(settings, client):
    response = post(client, "/tasks", {"title": "Prepare synthetic test checklist"})
    assert response.status_code == 201
    task = response.json()
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/v1/tasks/" + task["id"], headers=AUTH).json() == task
        assert restarted.get("/api/v1/tasks/" + task["id"], headers=PERSONAL).status_code == 404
        patched = restarted.patch(
            "/api/v1/tasks/" + task["id"],
            headers={**AUTH, "Idempotency-Key": "transition"},
            json={"status": "IN_PROGRESS"},
        )
        assert patched.status_code == 200
        invalid = restarted.patch(
            "/api/v1/tasks/" + task["id"],
            headers={**AUTH, "Idempotency-Key": "invalid"},
            json={"status": "TODO"},
        )
        assert invalid.status_code == 409
        receipts = restarted.get("/api/v1/receipts", headers=AUTH).json()["items"]
        assert len(receipts) == 2
        assert all(r["outcome"] == "COMPLETED" for r in receipts)
        assert all(r["actor_id"] == "synthetic-dani" for r in receipts)
        assert all(r["synthetic"] is True for r in receipts)


def test_idempotency_replay_and_payload_conflict(client):
    first = post(client, "/tasks", {"title": "Synthetic task"})
    assert post(client, "/tasks", {"title": "Synthetic task"}).json() == first.json()
    assert post(client, "/tasks", {"title": "Different"}).status_code == 409
    assert len(client.get("/api/v1/tasks", headers=AUTH).json()["items"]) == 1
    assert len(client.get("/api/v1/receipts", headers=AUTH).json()["items"]) == 1


def test_concurrent_replay_has_one_effect(app):
    def request(_):
        with TestClient(app) as client:
            return post(client, "/tasks", {"title": "Concurrent synthetic task"}).json()

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(request, range(4)))
    assert len({r["id"] for r in results}) == 1


def test_revenue_drafts_and_plan_capacity(client):
    goal = client.get("/api/v1/bakery/goals/weekly", headers=AUTH).json()
    assert goal["target_cents"] == 50000
    assert goal["actual_cents"] is None
    assert goal["source_status"] == "MISSING_SOURCE"
    draft = post(
        client,
        "/orders/drafts",
        {"description": "Synthetic order inquiry", "estimated_total_cents": 10000},
    )
    assert draft.status_code == 201 and draft.json()["status"] == "DRAFT"
    after = client.get("/api/v1/bakery/goals/weekly", headers=AUTH).json()
    assert after["actual_cents"] is None
    assert after["planned_draft_cents"] == 10000
    plan = post(
        client,
        "/bakery/plans",
        {"title": "Synthetic preparation", "minutes_required": 120, "capacity_minutes": 60},
        key="plan",
    )
    assert plan.status_code == 201
    assert plan.json()["status"] == "PROPOSED"
    assert plan.json()["capacity_status"] == "OVER_CAPACITY"
    assert (
        client.get("/api/v1/bakery/plans/" + plan.json()["id"], headers=AUTH).json() == plan.json()
    )
    batch = post(
        client,
        "/bakery/batch-plans",
        {
            "recipe_ref": "synthetic-v1",
            "base_yield": 12,
            "target_yield": 30,
            "ingredients": [{"name": "synthetic flour", "grams": "200"}],
        },
        key="batch",
    )
    assert batch.status_code == 201
    assert batch.json()["ingredients"][0]["grams"] == "500.000"
    assert batch.json()["integration_status"] == "LOCAL_CALCULATION_NOT_SCALED_BAKING"


def test_message_queued_and_external_effect_refused(client):
    message = post(client, "/assistant/messages", {"message": "Hello Darla"})
    assert message.status_code == 200 and message.json()["status"] == "QUEUED"
    refused = post(client, "/runs", {"tool": "customer.send", "arguments": {"approved": True}})
    assert refused.status_code == 403
    receipts = client.get("/api/v1/receipts", headers=AUTH).json()["items"]
    assert len(receipts) == 1 and receipts[0]["action"] == "bridge.message"


def test_run_worker_checkpoint_restart_and_cancel(client, app, settings):
    run = post(
        client, "/runs", {"tool": "task.create", "arguments": {"title": "Worker synthetic task"}}
    ).json()
    assert run["status"] == "QUEUED"
    assert Worker(app.state.services).tick() == 1
    with TestClient(create_app(settings)) as restarted:
        done = restarted.get("/api/v1/runs/" + run["id"], headers=AUTH).json()
        assert done["status"] == "COMPLETED"
        assert done["checkpoint"]["postcondition_verified"] is True
        assert Worker(restarted.app.state.services).tick() == 0
        assert len(restarted.get("/api/v1/tasks", headers=AUTH).json()["items"]) == 1
    second = post(
        client,
        "/runs",
        {"tool": "task.create", "arguments": {"title": "Cancelled task"}},
        key="second",
    ).json()
    cancel = post(client, "/runs/" + second["id"] + "/cancel", {}, key="cancel")
    assert cancel.status_code == 200 and cancel.json()["status"] == "CANCELLED"
    assert Worker(app.state.services).tick() == 0


def test_schedule_tick_is_durable_and_duplicate_safe(client, app):
    schedule = post(
        client,
        "/schedules",
        {
            "title": "Synthetic recurring reminder",
            "interval_seconds": 60,
            "task_title": "Synthetic daily checklist",
        },
    ).json()
    assert schedule["worker_operating"] is False
    worker = Worker(app.state.services)
    worker.tick()
    worker.tick()
    assert len(client.get("/api/v1/tasks", headers=AUTH).json()["items"]) == 1
    assert len(client.get("/api/v1/runs", headers=AUTH).json()["items"]) == 1


def test_memory_scope_correction_and_deletion(client, app, settings):
    memory = app.state.services.seed_synthetic_memory(
        "synthetic-dani", "personal", "Synthetic quiet hours", "fixture-v1"
    )
    assert client.get("/api/v1/memory", headers=AUTH).json()["items"] == []
    path = "/api/v1/memory/" + memory["id"]
    assert (
        client.patch(
            path, headers={**AUTH, "Idempotency-Key": "bad"}, json={"value": "stolen"}
        ).status_code
        == 404
    )
    changed = client.patch(
        path,
        headers={**PERSONAL, "Idempotency-Key": "correct"},
        json={"value": "Synthetic revised preference"},
    )
    assert changed.status_code == 200
    assert changed.json()["provenance"] == "user_correction"
    deleted = client.delete(path, headers={**PERSONAL, "Idempotency-Key": "delete"})
    assert deleted.status_code == 200
    with TestClient(create_app(settings)) as restarted:
        assert restarted.get("/api/v1/memory", headers=PERSONAL).json()["items"] == []
    assert "Synthetic revised preference" not in json.dumps(
        client.get("/api/v1/receipts", headers=PERSONAL).json()
    )


def test_approval_cannot_be_spoofed_or_replayed(client, app):
    approval = app.state.services.seed_synthetic_approval(
        "synthetic-dani", "bakery", "Synthetic review"
    )
    path = "/approvals/" + approval["id"] + "/decision"
    assert post(client, path, {"decision": "APPROVED", "principal": "Dani"}).status_code == 422
    response = post(client, path, {"decision": "APPROVED"}, key="approve")
    assert response.status_code == 200
    assert response.json()["execution_authorized"] is False
    assert post(client, path, {"decision": "REJECTED"}, key="redecision").status_code == 409


def test_journal_is_redacted_and_verifiable(client, app):
    post(client, "/tasks", {"title": "PRIVATE SYNTHETIC TEXT"})
    receipt = client.get("/api/v1/receipts", headers=AUTH).json()["items"][0]
    app.state.services.flush_journal()
    journal = app.state.services.settings.state_dir / "receipts" / (receipt["id"] + ".json")
    assert journal.is_file()
    assert "PRIVATE SYNTHETIC TEXT" not in journal.read_text()
    assert json.loads(journal.read_text())["receipt_id"] == receipt["id"]


def test_default_auth_fails_closed(settings):
    settings.synthetic_mode = False
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/tasks", headers=AUTH).status_code == 401


def test_paths_cannot_escape_axiom(settings):
    with pytest.raises(ValueError):
        Settings(root=ROOT, state_dir=ROOT.parent / "escape")
