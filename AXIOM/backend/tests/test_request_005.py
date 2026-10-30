"""Request 005 public API acceptance. All state is fresh synthetic test data."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from test_runtime_http import proposal

from backend.app.domain import Domain
from backend.app.main import create_app
from backend.app.security import SCOPES, Principal
from backend.app.worker import Worker

AGENT = {"Authorization": "Bearer synthetic-agent-bakery"}
HUMAN = {"Authorization": "Bearer synthetic-dani-bakery"}
GUARD = {"Authorization": "Bearer synthetic-guard-bakery"}


def post(client, path, body, headers=AGENT, key=None):
    return client.post(
        "/api/v1/runtime/" + path,
        json=body,
        headers={**headers, "Idempotency-Key": key or str(uuid4())},
    )


def fresh(app):
    # Explicit storage fixture representing an already-accepted, bound health report.
    # This is not a model call or a new production bootstrap privilege.
    p = Principal("synthetic-dani", Domain.BAKERY, SCOPES)
    with app.state.services.store.transaction() as c:
        return app.state.services._create(
            c,
            "runtime_health",
            p,
            {
                "session_id": "synthetic-agent-bakery",
                "expires_at": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
                "fixture": "request005-synthetic-preexisting-health",
            },
        )


def feed(client, headers=GUARD, **params):
    response = client.get("/api/v1/runtime/events", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def propose(client, tool="task.create", name=None):
    body = proposal(name or uuid4().hex)
    params = {
        "task.create": {"title": "SYNTHETIC_PAYLOAD_CANARY"},
        "plan.create": {"title": "SYNTHETIC_PAYLOAD_CANARY", "minutes_required": 20},
        "batch.create": {
            "recipe_ref": "synthetic-v1",
            "base_yield": 1,
            "target_yield": 2,
            "ingredients": [{"name": "flour", "grams": "10"}],
        },
        "draft.create": {"description": "SYNTHETIC_PAYLOAD_CANARY"},
    }
    body["input"]["tool_allowlist"] = [tool]
    body["input"]["authorization"]["permitted_scopes"] += ["plan.write", "draft.write"]
    body["result"]["action"].update(
        requested_tool=tool, parameters=params[tool], requires_approval=False
    )
    return post(client, "proposals", body)


def test_empty_state_refuses_stale_agent_admission(client):
    response = post(client, "proposals", proposal())
    assert response.status_code == 423, response.text
    assert response.json()["error"]["code"] == "RUNTIME_STALE"


@pytest.mark.parametrize("tool", ["task.create", "plan.create", "batch.create", "draft.create"])
def test_lock_refuses_all_tiers_and_feed_contains_one_refusal(client, app, tool):
    fresh(app)
    assert post(client, "lock", {"reason": "Synthetic guard reason"}, GUARD).status_code == 200
    response = propose(client, tool)
    assert response.status_code == 423 and response.json()["error"]["code"] == "AGENT_LOCKED"
    rows = [r for r in feed(client)["items"] if r["type"] == "proposal_refused"]
    assert len(rows) == 1 and rows[0]["code"] == "AGENT_LOCKED" and rows[0]["tool"] == tool
    assert client.get("/api/v1/runs", headers=HUMAN).json()["items"] == []


@pytest.mark.parametrize("tool", ["task.create", "plan.create", "batch.create", "draft.create"])
def test_fresh_health_admits_original_auto_and_ask_policy(client, app, tool):
    fresh(app)
    response = propose(client, tool)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == ("BLOCKED" if tool == "draft.create" else "QUEUED")


def test_stale_expiry_boundary_and_lapsed_feed_observation(client, app, monkeypatch):
    from backend.app import gate

    health = fresh(app)
    moment = datetime.fromisoformat(health["expires_at"])
    monkeypatch.setattr(gate, "clock", lambda: moment)
    assert post(client, "proposals", proposal()).json()["error"]["code"] == "RUNTIME_STALE"
    first = feed(client)["items"]
    assert len([r for r in first if r["type"] == "heartbeat_lapsed"]) == 1
    assert (
        len([r for r in first if r["type"] == "proposal_refused" and r["code"] == "RUNTIME_STALE"])
        == 1
    )
    assert feed(client)["items"] == first


def test_returning_validated_heartbeat_does_not_clear_lock(client, app):
    fresh(app)
    body = proposal("bound-heartbeat")
    body["result"]["test_double"] = False
    run = post(client, "proposals", body).json()
    assert Worker(app.state.services).tick() == 1
    assert post(client, "lock", {"reason": "Synthetic lock"}, GUARD).status_code == 200
    health = post(
        client,
        "health",
        {
            "run_id": run["id"],
            "request_id": "bound-heartbeat",
            "evidence_reference": "synthetic-test-only",
            "evidence_sha256": "a" * 64,
        },
    )
    assert health.status_code == 200, health.text
    status = client.get("/api/v1/agent/status", headers=GUARD).json()
    assert status["status"] == "DEGRADED" and status["locked"] is True
    assert propose(client).status_code == 423


@pytest.mark.parametrize("headers", [AGENT, GUARD])
def test_agent_and_guard_cannot_rearm(client, headers):
    assert post(client, "rearm", {"note": "Forged rearm"}, headers).status_code == 403


def test_agent_cannot_lock(client):
    assert post(client, "lock", {"reason": "Self-lock"}).status_code == 403


@pytest.mark.parametrize("body", [{}, {"note": ""}, {"note": "   "}])
def test_rearm_requires_nonblank_note(client, body):
    assert post(client, "rearm", body, HUMAN).status_code == 422


def test_rearm_receipt_human_pending_decisions_and_still_stale(client, app):
    fresh(app)
    run = propose(client, "draft.create").json()
    assert post(client, "lock", {"reason": "Synthetic guard reason"}, GUARD).status_code == 200
    response = client.post(
        "/api/v1/approvals/" + run["approval_id"] + "/decision",
        headers={**HUMAN, "Idempotency-Key": "human-decision"},
        json={"decision": "APPROVED"},
    )
    assert response.status_code == 200, response.text
    result = post(
        client, "rearm", {"note": "Human inspected the synthetic refusal."}, HUMAN, key="rearm-one"
    )
    assert result.status_code == 200, result.text
    assert result.json()["locked"] is False
    receipts = client.get("/api/v1/receipts", headers=HUMAN).json()["items"]
    r = next(r for r in receipts if r["id"] == result.json()["receipt_id"])
    assert (
        r["note"] == "Human inspected the synthetic refusal."
        and r["human_principal"] == "synthetic-dani"
    )
    assert (
        post(client, "rearm", {"note": r["note"]}, HUMAN, key="rearm-one").json() == result.json()
    )
    assert len([r for r in feed(client)["items"] if r["type"] == "agent_rearmed"]) == 1


@pytest.mark.parametrize(
    "method,path",
    [
        ("GET", "/api/v1/tasks"),
        ("GET", "/api/v1/runtime/tools"),
        ("GET", "/api/v1/receipts"),
        ("GET", "/api/v1/runtime/journal-anchors"),
        ("POST", "/api/v1/runtime/proposals"),
        ("POST", "/api/v1/runtime/health"),
        ("POST", "/api/v1/runtime/rearm"),
        ("POST", "/api/v1/approvals/x/decision"),
    ],
)
def test_guard_has_only_the_three_authorized_surfaces(client, method, path):
    response = client.request(method, path, headers=GUARD, json={} if method == "POST" else None)
    assert response.status_code == 403, response.text


def test_anchor_auth_shape_equal_sequence_and_conflicting_hashes(client):
    body = {"journal_id": "journal005", "seq": 4, "head_hash": "a" * 64}
    first = post(client, "journal-anchors", body)
    assert first.status_code == 201 and set(first.json()) == {"id", "server_time"}
    same = post(client, "journal-anchors", body)
    fork = post(client, "journal-anchors", {**body, "head_hash": "b" * 64})
    assert same.status_code == fork.status_code == 201
    assert len({first.json()["id"], same.json()["id"], fork.json()["id"]}) == 3
    low = post(client, "journal-anchors", {**body, "seq": 3})
    assert low.status_code == 409 and low.json()["error"]["code"] == "ANCHOR_SEQUENCE_REGRESSION"
    assert client.get("/api/v1/runtime/journal-anchors", headers=AGENT).status_code == 403
    assert post(client, "journal-anchors", body, HUMAN).status_code == 403
    rows = client.get(
        "/api/v1/runtime/journal-anchors",
        headers=HUMAN,
        params={"journal_id": "journal005", "since": first.json()["server_time"]},
    ).json()["items"]
    assert len(rows) == 3
    assert (
        client.get(
            "/api/v1/runtime/journal-anchors",
            headers={"Authorization": "Bearer synthetic-dani-personal"},
        ).json()["items"]
        == []
    )
    assert (
        client.get(
            "/api/v1/runtime/journal-anchors", headers=HUMAN, params={"journal_id": "other"}
        ).json()["items"]
        == []
    )
    for method in ("PUT", "PATCH", "DELETE"):
        assert client.request(
            method, "/api/v1/runtime/journal-anchors/" + first.json()["id"], headers=AGENT
        ).status_code in {404, 405}


@pytest.mark.parametrize(
    "change",
    [
        {"seq": 0},
        {"seq": True},
        {"seq": 1.2},
        {"head_hash": "A" * 64},
        {"head_hash": "abc"},
        {"journal_id": ""},
        {"extra": "refused"},
    ],
)
def test_anchor_input_validation(client, change):
    body = {"journal_id": "j005", "seq": 1, "head_hash": "a" * 64, **change}
    assert post(client, "journal-anchors", body).status_code == 422


@pytest.mark.parametrize("headers,status", [(GUARD, 200), (HUMAN, 200), (AGENT, 403), ({}, 401)])
def test_feed_auth(client, headers, status):
    assert client.get("/api/v1/runtime/events", headers=headers).status_code == status


@pytest.mark.parametrize("params", [{"since": -1}, {"since": "abc"}, {"limit": 0}, {"limit": 501}])
def test_feed_limit_and_cursor_validation(client, params):
    assert client.get("/api/v1/runtime/events", headers=GUARD, params=params).status_code == 422


def test_feed_is_audit_projection_ordered_resumable_and_payload_free(client, app):
    from backend.app.gate_state import audit

    fresh(app)
    post(client, "lock", {"reason": "REASON_CANARY"}, GUARD)
    propose(client)
    post(client, "rearm", {"note": "NOTE_CANARY"}, HUMAN)
    rows = feed(client)["items"]
    assert [r["type"] for r in rows] == ["agent_locked", "proposal_refused", "agent_rearmed"]
    assert [r["id"] for r in rows] == sorted(r["id"] for r in rows)
    assert all(set(r) == {"id", "at", "type", "code", "agent_id", "tool"} for r in rows)
    assert not any(
        c in json.dumps(rows)
        for c in [
            "REASON_CANARY",
            "NOTE_CANARY",
            "SYNTHETIC_PAYLOAD_CANARY",
            "Bearer",
            "synthetic-agent-bakery",
        ]
    )
    page = feed(client, limit=1)
    assert len(page["items"]) == 1
    assert feed(client, since=page["next_since"])["items"] == rows[1:]
    with app.state.services.store.engine.connect() as c:
        facts = [dict(r) for r in c.execute(select(audit)).mappings()]
    assert next(r for r in facts if r["type"] == "agent_locked")["reason"] == "REASON_CANARY"
    assert all(
        any(f["id"] == r["id"] and all(f[k] == v for k, v in r.items()) for f in facts)
        for r in rows
    )


def test_auto_cap_yields_exactly_one_refusal_event(client, app):
    fresh(app)
    for i in range(20):
        assert propose(client, name="cap005-" + str(i)).status_code == 201
    refused = propose(client, name="cap005-21")
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "AUTO_RATE_LIMIT"
    rows = [r for r in feed(client)["items"] if r["type"] == "proposal_refused"]
    assert len(rows) == 1 and rows[0]["code"] == "AUTO_RATE_LIMIT"


def test_lock_idempotency_and_restart_preserve_all_state(client, app, settings):
    post(client, "journal-anchors", {"journal_id": "persist", "seq": 1, "head_hash": "a" * 64})
    post(client, "lock", {"reason": "persist"}, GUARD)
    post(client, "lock", {"reason": "persist"}, GUARD)
    with TestClient(create_app(settings)) as other:
        assert other.get("/api/v1/agent/status", headers=GUARD).json()["locked"] is True
        assert len(other.get("/api/v1/runtime/journal-anchors", headers=HUMAN).json()["items"]) == 1
        assert len([r for r in feed(other)["items"] if r["type"] == "agent_locked"]) == 1


def test_tables_are_append_only(client, app):
    from backend.app.gate_state import anchors, audit

    post(client, "journal-anchors", {"journal_id": "immutable", "seq": 1, "head_hash": "a" * 64})
    for table in (audit, anchors):
        for statement in (
            update(table).values(**({"code": "CHANGED"} if table is audit else {"seq": 9})),
            delete(table),
        ):
            with pytest.raises(IntegrityError), app.state.services.store.transaction() as c:
                c.execute(statement)


def test_concurrent_lock_and_admission_have_a_serial_commit_order(client, app):
    fresh(app)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda x: post(client, "lock", {"reason": "race"}, GUARD) if x else propose(client),
                [0, 1],
            )
        )
    assert results[0].status_code in {201, 423} and results[1].status_code == 200
    assert propose(client).status_code == 423


@pytest.mark.parametrize("headers", [HUMAN, AGENT, GUARD])
def test_corrected_real_status_route_has_locked_and_old_envelope(client, headers):
    response = client.get("/api/v1/agent/status", headers=headers)
    assert response.status_code == 200
    assert response.json()["locked"] is False
    assert response.json()["contract_version"] == "darla-runtime/1"
    assert client.get("/api/v1/runtime/status", headers=headers).status_code == 404


def test_corrected_status_auth_and_guard_redaction(client, app):
    assert client.get("/api/v1/agent/status").status_code == 401
    assert (
        client.get("/api/v1/agent/status", headers={"Authorization": "Bearer bad"}).status_code
        == 401
    )
    fresh(app)
    human = client.get("/api/v1/agent/status", headers=HUMAN).json()
    assert human["evidence"] and human["status"] == "DEGRADED"
    guard = client.get("/api/v1/agent/status", headers=GUARD).json()
    assert guard["status"] == "DEGRADED" and guard["evidence"] == []
    assert "session_id" not in json.dumps(guard) and "fixture" not in json.dumps(guard)
    schema = client.get("/openapi.json").json()
    assert "/api/v1/runtime/status" not in schema["paths"]
    assert (
        schema["components"]["schemas"]["AgentStatus"]["properties"]["locked"]["type"] == "boolean"
    )
