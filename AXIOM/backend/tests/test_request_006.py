"""R-BOOT acceptance; no legacy healthy-admission fixture applies here."""

from datetime import datetime, timedelta

import pytest
from test_request_005 import AGENT, GUARD, HUMAN, feed, fresh, post, propose
from test_runtime_http import proposal

from backend.app import gate
from backend.app.worker import Worker


@pytest.mark.parametrize("headers", [AGENT, GUARD])
def test_bootstrap_human_only(client, headers):
    assert post(client, "bootstrap", {"note": "No authority"}, headers).status_code == 403


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"note": ""},
        {"note": "   "},
        {"note": "x", "minutes": 0},
        {"note": "x", "minutes": 16},
        {"note": "x", "minutes": True},
        {"note": "x", "minutes": 1.5},
    ],
)
def test_bootstrap_validation(client, body):
    assert post(client, "bootstrap", body, HUMAN).status_code == 422


@pytest.mark.parametrize("tool", ["task.create", "draft.create"])
def test_bootstrap_starts_first_auto_and_ask_action(client, app, tool):
    assert propose(client, tool).status_code == 423
    result = post(client, "bootstrap", {"note": "Human inspected synthetic startup"}, HUMAN)
    assert result.status_code == 200, result.text
    assert datetime.fromisoformat(result.json()["expires_at"]) - gate.clock() > timedelta(minutes=9)
    status = client.get("/api/v1/agent/status", headers=HUMAN).json()
    assert (
        status["runtime_evidence"] == "HUMAN_ATTESTED"
        and status["evidence_source"] == "HUMAN_ATTESTED_BOOTSTRAP"
    )
    assert status["runtime_verified_by_axiom"] is False
    run = propose(client, tool).json()
    if tool == "draft.create":
        assert run["status"] == "BLOCKED"
        approved = client.post(
            "/api/v1/approvals/" + run["approval_id"] + "/decision",
            headers={**HUMAN, "Idempotency-Key": "boot-approval"},
            json={"decision": "APPROVED"},
        )
        assert approved.status_code == 200
    assert Worker(app.state.services).tick() == 1
    assert client.get("/api/v1/runs/" + run["id"], headers=HUMAN).json()["status"] == "COMPLETED"


def test_bootstrap_window_expires_and_never_unlocks(client, monkeypatch):
    result = post(client, "bootstrap", {"note": "one minute", "minutes": 1}, HUMAN).json()
    post(client, "lock", {"reason": "guard holds"}, GUARD)
    assert propose(client).json()["error"]["code"] == "AGENT_LOCKED"
    post(client, "rearm", {"note": "human clears lock"}, HUMAN)
    monkeypatch.setattr(gate, "clock", lambda: datetime.fromisoformat(result["expires_at"]))
    assert propose(client).json()["error"]["code"] == "RUNTIME_STALE"


def test_bootstrap_single_active_and_receipt_event_link(client):
    first = post(
        client, "bootstrap", {"note": "PRIVATE_NOTE_CANARY"}, HUMAN, key="bootstrap-replay"
    )
    assert first.status_code == 200
    assert (
        post(
            client, "bootstrap", {"note": "PRIVATE_NOTE_CANARY"}, HUMAN, key="bootstrap-replay"
        ).json()
        == first.json()
    )
    assert post(client, "bootstrap", {"note": "second active"}, HUMAN).status_code == 409
    events = [r for r in feed(client)["items"] if r["type"] == "bootstrap_attested"]
    assert len(events) == 1 and "PRIVATE_NOTE_CANARY" not in str(events)
    receipts = client.get("/api/v1/receipts", headers=HUMAN).json()["items"]
    receipt = next(r for r in receipts if r["id"] == first.json()["receipt_id"])
    assert (
        receipt["note"] == "PRIVATE_NOTE_CANARY" and receipt["human_principal"] == "synthetic-dani"
    )


def test_real_health_supersedes_bootstrap_permanently(client, app, monkeypatch):
    post(client, "bootstrap", {"note": "boot", "minutes": 15}, HUMAN)
    body = proposal("boot-real-health")
    body["result"]["test_double"] = False
    run = post(client, "proposals", body).json()
    assert Worker(app.state.services).tick() == 1
    result = post(
        client,
        "health",
        {
            "run_id": run["id"],
            "request_id": "boot-real-health",
            "evidence_reference": "synthetic-test-only",
            "evidence_sha256": "a" * 64,
        },
    )
    assert result.status_code == 200
    status = client.get("/api/v1/agent/status", headers=HUMAN).json()
    assert (
        status["evidence_source"] == "RUNTIME_REPORTED"
        and status["runtime_evidence"] == "RUNTIME_REPORTED_NOT_VERIFIED_BY_AXIOM"
    )
    assert post(client, "bootstrap", {"note": "fresh real blocks"}, HUMAN).status_code == 409
    monkeypatch.setattr(
        gate,
        "clock",
        lambda: datetime.fromisoformat(result.json()["expires_at"]) + timedelta(seconds=1),
    )
    assert propose(client).json()["error"]["code"] == "RUNTIME_STALE"


def test_fresh_real_health_blocks_bootstrap(client, app):
    fresh(app)
    assert post(client, "bootstrap", {"note": "not needed"}, HUMAN).status_code == 409


def test_any_fresh_real_health_blocks_bootstrap_even_with_a_newer_expired_row(client, app):
    from backend.app.domain import Domain
    from backend.app.security import SCOPES, Principal

    fresh(app)
    with app.state.services.store.transaction() as c:
        app.state.services._create(
            c,
            "runtime_health",
            Principal("synthetic-dani", Domain.BAKERY, SCOPES),
            {
                "session_id": "synthetic-agent-bakery",
                "expires_at": (gate.clock() - timedelta(seconds=1)).isoformat(),
                "fixture": "newer-but-expired",
            },
        )
    result = post(client, "bootstrap", {"note": "Real fresh evidence must win"}, HUMAN)
    assert (
        result.status_code == 409
        and result.json()["error"]["code"] == "RUNTIME_HEALTH_ALREADY_FRESH"
    )
