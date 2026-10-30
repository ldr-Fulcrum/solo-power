"""Request 007 acceptance through the real registry/gate/approval/worker seams."""

import json
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from bakeoff_stub import ATTACK, TOKEN, Hub
from sqlalchemy import select
from test_request_005 import AGENT, GUARD, HUMAN, post
from test_runtime_http import proposal

from backend.app.bakeoff_client import CHANGES, READS, Adapter, BakeoffFailure, pinned_tools
from backend.app.bakeoff_worker import Worker
from backend.app.domain import Domain
from backend.app.persistence import records
from backend.app.security import Principal

STUB_URL = "http://task-hub.test/mcp"


@pytest.fixture(autouse=True)
def optional_adapter_fixture_configuration(monkeypatch, request):
    if request.node.name != "test_optional_real_hub_read_only":
        monkeypatch.setenv("DARLA_BAKEOFF_URL", STUB_URL)
        monkeypatch.delenv("DARLA_BAKEOFF_TOKEN_ENV", raising=False)


ARGS = {
    "whoami": {},
    "get_dashboard": {},
    "list_tasks": {},
    "get_task": {"task_id": 1},
    "list_incidents": {},
    "list_equipment": {},
    "update_task_status": {
        "task_id": 1,
        "status": "In Progress",
        "version": 1,
        "idempotency_key": "model-hint-unused",
    },
    "claim_task": {"task_id": 1, "idempotency_key": "model-hint-unused"},
    "create_incident": {
        "category": "Equipment",
        "description": "Synthetic fault",
        "idempotency_key": "model-hint-unused",
    },
    "log_maintenance": {
        "equipment_id": 1,
        "description": "Synthetic check",
        "idempotency_key": "model-hint-unused",
    },
}


def setup(client, app, role="STAFF", boot=True):
    hub = Hub(role)
    adapter = Adapter(STUB_URL, TOKEN, transport=httpx.MockTransport(hub)).start()
    app.state.services.bakeoff = adapter
    app.state.services.bakeoff_error = ""
    if boot:
        assert (
            post(client, "bootstrap", {"note": "Synthetic stub runtime check"}, HUMAN).status_code
            == 200
        )
    return hub, adapter


def propose(client, name="list_tasks", args=None, identifier=None):
    body = proposal(identifier or uuid4().hex)
    body["input"]["tool_allowlist"] = ["bakeoff." + name]
    body["result"]["action"].update(
        requested_tool="bakeoff." + name,
        parameters=ARGS[name] if args is None else args,
        requires_approval=False,
    )
    return post(client, "proposals", body)


def approve(client, run):
    response = client.post(
        "/api/v1/approvals/" + run["approval_id"] + "/decision",
        headers={**HUMAN, "Idempotency-Key": uuid4().hex},
        json={"decision": "APPROVED"},
    )
    assert response.status_code == 200, response.text


def stored(client, run):
    return client.get("/api/v1/runs/" + run["id"], headers=HUMAN).json()


@pytest.mark.parametrize("name", sorted(READS | CHANGES))
def test_pinned_tiers_and_approval_before_each_change(client, app, name):
    hub, _ = setup(client, app)
    tools = client.get("/api/v1/runtime/tools", headers=AGENT).json()
    spec = next(t for t in pinned_tools() if t["name"] == name)
    assert tools["bakeoff." + name]["arguments"] == spec["inputSchema"]
    assert tools["bakeoff." + name]["tier"] == ("ASK" if name in CHANGES else "AUTO")
    run = propose(client, name).json()
    if name in CHANGES:
        assert run["status"] == "BLOCKED"
        assert Worker(app.state.services).tick() == 0
        assert not any(n == name for n, a in hub.calls)
        approve(client, run)
    else:
        assert run["status"] == "QUEUED"
    assert Worker(app.state.services).tick() == 1
    result = stored(client, run)
    assert result["status"] == "COMPLETED"
    assert (
        result["bakeoff"]["tool"] == "bakeoff." + name and result["bakeoff"]["http_status"] == 200
    )
    if name in CHANGES:
        actual = next(a for n, a in hub.calls if n == name)
        assert actual["idempotency_key"] == "axiom:" + run["id"]
        assert result["bakeoff"]["receipt_id"]


@pytest.mark.parametrize("role", ["OWNER", "MANAGER"])
def test_privileged_token_enables_reads_only_with_warning(client, app, role):
    setup(client, app, role)
    tools = client.get("/api/v1/runtime/tools", headers=AGENT).json()
    assert {n.removeprefix("bakeoff.") for n in tools if n.startswith("bakeoff.")} == READS
    assert propose(client, "create_incident").status_code == 403
    assert (
        "STAFF_TOKEN_REQUIRED"
        in client.get("/api/v1/integrations", headers=HUMAN).json()["bakeoff"]
    )


def test_role_is_rechecked_before_change(client, app):
    hub, _ = setup(client, app)
    run = propose(client, "create_incident").json()
    approve(client, run)
    hub.role = "OWNER"
    assert Worker(app.state.services).tick() == 0
    assert stored(client, run)["error_code"] == "BAKEOFF_STAFF_REQUIRED"
    assert not any(n == "create_incident" for n, a in hub.calls)


@pytest.mark.parametrize(
    "failure,code",
    [
        (401, "BAKEOFF_UNAUTHORIZED"),
        (403, "BAKEOFF_FORBIDDEN"),
        (404, "BAKEOFF_NOT_FOUND"),
        (409, "BAKEOFF_CONFLICT"),
        (422, "BAKEOFF_VALIDATION"),
        (429, "BAKEOFF_RATE_LIMIT"),
        ("connect", "BAKEOFF_UNREACHABLE"),
        ("timeout", "BAKEOFF_TIMEOUT"),
    ],
)
def test_failure_classes_are_recorded_without_remote_message(client, app, failure, code):
    hub, _ = setup(client, app)
    run = propose(client).json()
    hub.failure = failure
    Worker(app.state.services).tick()
    result = stored(client, run)
    assert (
        result["status"] == "FAILED"
        and result["error_code"] == code
        and code in result["error_message"]
    )
    assert TOKEN not in json.dumps(result)


@pytest.mark.parametrize("status", [401, 403, 404, 409, 422, 429])
def test_http_envelope_failures_are_also_classified(client, app, status):
    hub, _ = setup(client, app)
    run = propose(client).json()
    hub.failure = ("http", status)
    Worker(app.state.services).tick()
    assert (
        stored(client, run)["status"] == "FAILED"
        and stored(client, run)["bakeoff"]["http_status"] == status
    )


def test_unknown_change_has_no_retry_and_cannot_hide_its_attempt(client, app):
    hub, _ = setup(client, app)
    run = propose(client, "create_incident").json()
    approve(client, run)
    hub.failure = "timeout"
    worker = Worker(app.state.services)
    worker.tick()
    assert stored(client, run)["status"] == "UNRESOLVED"
    for _ in range(3):
        worker.tick()
    assert len([n for n, a in hub.calls if n == "create_incident"]) == 1
    cancel = client.post(
        "/api/v1/runs/" + run["id"] + "/cancel",
        headers={**HUMAN, "Idempotency-Key": "cancel-unknown"},
        json={},
    )
    assert cancel.status_code == 409


def test_wire_idempotency_is_run_derived_and_replay_safe():
    hub = Hub()
    adapter = Adapter(STUB_URL, TOKEN, transport=httpx.MockTransport(hub)).start()
    runid = uuid4().hex
    args = ARGS["create_incident"]
    try:
        first = adapter.call("create_incident", args, runid)
        again = adapter.call("create_incident", args, runid)
        assert again["replayed"] is True and first["receipt_id"] == again["receipt_id"]
        with pytest.raises(BakeoffFailure, match="BAKEOFF_CONFLICT"):
            adapter.call("create_incident", {**args, "description": "changed"}, runid)
    finally:
        adapter.close()


def test_result_is_only_untrusted_text_and_token_never_persists(client, app, settings):
    hub, _ = setup(client, app)
    hub.echo = True
    run = propose(client).json()
    Worker(app.state.services).tick()
    result = stored(client, run)
    assert result["checkpoint"]["untrusted_text_id"]
    texts = client.get("/api/v1/runtime/untrusted-texts", headers=AGENT).json()["items"]
    assert texts[0]["trust"] == "UNTRUSTED_DATA" and texts[0]["instruction_authority"] == "NONE"
    assert "[REDACTED]" in texts[0]["text"]
    assert ATTACK not in json.dumps(result)
    with app.state.services.store.engine.connect() as c:
        assert TOKEN not in str(list(c.execute(select(records.c.body)).scalars()))
    for f in settings.state_dir.rglob("*"):
        if f.is_file():
            assert TOKEN.encode() not in f.read_bytes()


def test_injected_result_is_not_a_trusted_run_field(client, app):
    setup(client, app)
    run = propose(client).json()
    Worker(app.state.services).tick()
    texts = client.get("/api/v1/runtime/untrusted-texts", headers=AGENT).json()["items"]
    assert ATTACK in texts[0]["text"] and ATTACK not in json.dumps(stored(client, run))


def test_schema_drift_is_reported_before_enablement():
    hub = Hub()
    hub.drift = True
    adapter = Adapter(STUB_URL, TOKEN, transport=httpx.MockTransport(hub))
    try:
        with pytest.raises(BakeoffFailure, match="BAKEOFF_SCHEMA_DRIFT"):
            adapter.start()
        assert adapter.ready is False
    finally:
        adapter.close()


def test_network_runs_outside_the_database_transaction(client, app):
    hub, _ = setup(client, app)
    run = propose(client, "create_incident").json()
    approve(client, run)
    checked = []

    def check(name, args):
        with app.state.services.store.engine.connect() as c:
            c.exec_driver_sql("PRAGMA busy_timeout=20")
            c.exec_driver_sql("BEGIN IMMEDIATE")
            c.rollback()
            checked.append(True)

    hub.callback = check
    assert Worker(app.state.services).tick() == 1 and checked


def test_gate_and_bootstrap_are_still_required(client, app):
    setup(client, app, boot=False)
    assert propose(client).json()["error"]["code"] == "RUNTIME_STALE"
    post(client, "bootstrap", {"note": "operator checked startup"}, HUMAN)
    post(client, "lock", {"reason": "guard stopped proposals"}, GUARD)
    assert propose(client).json()["error"]["code"] == "AGENT_LOCKED"


def test_recovered_inflight_attempt_is_unresolved_without_calling_hub(client, app):
    hub, _ = setup(client, app)
    run = propose(client, "create_incident").json()
    approve(client, run)
    with app.state.services.store.transaction() as c:
        value = app.state.services._get(
            c, "run", run["id"], Principal("synthetic-dani", Domain.BAKERY, frozenset({"read"}))
        )
        app.state.services._save(
            c,
            {
                **value,
                "status": "RUNNING",
                "attempts": 1,
                "lease_until": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                "checkpoint": {"external_attempt_started": True},
            },
        )
    Worker(app.state.services).tick()
    assert stored(client, run)["status"] == "UNRESOLVED" and not any(
        n == "create_incident" for n, a in hub.calls
    )


@pytest.mark.skipif(
    not os.environ.get("DARLA_BAKEOFF_URL"), reason="Real hub integration is explicit opt-in"
)
def test_optional_real_hub_read_only():
    adapter = Adapter.from_environment()
    assert adapter is not None
    try:
        assert adapter.start().role in {"STAFF", "OWNER", "MANAGER"}
    finally:
        adapter.close()


@pytest.mark.parametrize(
    "name",
    [
        "unregistered_action",
        "unregistered_update",
        "unregistered_list",
        "unregistered_export",
        "unregistered_assistant",
    ],
)
def test_unapproved_tools_never_reach_hub(client, app, name):
    hub, _ = setup(client, app)
    body = proposal(uuid4().hex)
    body["input"]["tool_allowlist"] = ["bakeoff." + name]
    body["result"]["action"].update(requested_tool="bakeoff." + name, parameters={})
    assert post(client, "proposals", body).status_code == 403
    assert not any(n == name for n, a in hub.calls)


@pytest.mark.parametrize("name", ["claim_task", "log_maintenance"])
def test_remote_policy_denial_is_preserved_not_bypassed(client, app, name):
    hub, _ = setup(client, app)
    run = propose(client, name).json()
    approve(client, run)
    hub.failure = 403
    Worker(app.state.services).tick()
    assert stored(client, run)["error_code"] == "BAKEOFF_FORBIDDEN"
