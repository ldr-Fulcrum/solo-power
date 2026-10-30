import ast
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_backend import AUTH
from test_loop_v4 import _principal, post
from test_runtime_http import AGENT, proposal

from backend.app import approval_policy as policy
from backend.app.worker import Worker

CASES = [
    ("task.create", {"title": "Policy test task"}),
    ("plan.create", {"title": "Policy plan", "minutes_required": 60}),
    (
        "batch.create",
        {
            "recipe_ref": "synthetic",
            "base_yield": 2,
            "target_yield": 3,
            "ingredients": [{"name": "Flour", "grams": "100"}],
        },
    ),
]


def body(tool, args, flag=False, request_id="policy-test"):
    value = proposal(request_id)
    value["input"]["tool_allowlist"] = [tool]
    value["input"]["authorization"]["permitted_scopes"] += ["plan.write", "draft.write"]
    value["result"]["action"].update(
        requested_tool=tool, parameters=json.loads(json.dumps(args)), requires_approval=flag
    )
    return value


def send(client, value, key="policy-test"):
    return post(client, "/runtime/proposals", value, key=key, headers=AGENT)


@pytest.mark.parametrize("tool,args", CASES)
def test_auto_false_executes_with_explicit_policy_receipts(client, app, tool, args):
    response = send(client, body(tool, args))
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "QUEUED" and "approval_id" not in run
    assert client.get("/api/v1/approvals", headers=AUTH).json()["items"] == []
    assert Worker(app.state.services).tick() == 1
    receipts = client.get("/api/v1/receipts", headers=AUTH).json()["items"]
    bound = [r for r in receipts if r["resource_id"] == run["id"]]
    assert {r["action"] for r in bound} == {"run.create", "run.execute"}
    for receipt in bound:
        assert receipt["approval"] == "AUTO_BY_POLICY"
        assert receipt["policy_version"] == policy.VERSION
        assert receipt["tool_tier"] == "AUTO"


@pytest.mark.parametrize("tool,args", CASES)
def test_true_raises_auto_to_pending_human_approval(client, app, tool, args):
    run = send(client, body(tool, args, True)).json()
    assert run["status"] == "BLOCKED" and run["approval"] == "HUMAN_REQUIRED"
    approval = app.state.services.get("approval", run["approval_id"], _principal())
    assert approval["status"] == "PENDING"
    assert Worker(app.state.services).tick() == 0
    path = "/approvals/" + approval["id"] + "/decision"
    assert post(client, path, {"decision": "APPROVED"}, headers=AGENT).status_code == 403
    assert post(client, path, {"decision": "APPROVED"}).status_code == 200
    assert Worker(app.state.services).tick() == 1


def test_model_cannot_lower_ask_or_supply_policy_fields(client, app):
    value = body("draft.create", {"description": "Nonbinding synthetic draft"})
    value["input"]["task_state"] = {"policy": {"draft.create": "AUTO"}}
    response = send(client, value)
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "BLOCKED" and run["tool_tier"] == "ASK"
    assert (
        app.state.services.get("approval", run["approval_id"], _principal())["status"] == "PENDING"
    )
    assert Worker(app.state.services).tick() == 0
    forged = body(
        "draft.create", {"description": "Untrusted policy field"}, request_id="policy-forge"
    )
    forged["result"]["action"]["tier"] = "AUTO"
    assert send(client, forged).status_code == 422
    forged = body("task.create", {"title": "Forged reference"}, request_id="policy-reference")
    forged["input"]["authorization"]["approval_reference"] = "model-claims-approved"
    assert send(client, forged).status_code == 403


def test_effective_policy_is_published(client):
    tools = client.get("/api/v1/runtime/tools", headers=AGENT).json()
    for name, tier in policy.TIERS.items():
        assert tools[name]["tier"] == tier
        assert tools[name]["approval_required"] == (tier == "ASK")
        assert tools[name]["policy_version"] == policy.VERSION
        assert tools[name]["auto_limit"] == 20
        assert tools[name]["auto_window_seconds"] == 600


def test_auto_cap_is_atomic_replay_safe_counts_cancels_and_expires(client, app):
    first = None
    for index in range(19):
        value = body("task.create", {"title": "Capped task"}, request_id=f"cap-{index}")
        response = send(client, value)
        assert response.status_code == 201, response.text
        if index == 0:
            first = (value, response.json())
    assert send(client, first[0], key="different-key").json() == first[1]

    def submit(index):
        return send(
            client, body("task.create", {"title": "Atomic cap"}, request_id=f"last-{index}")
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, [1, 2])) == [201, 429]
    assert post(client, "/runs/" + first[1]["id"] + "/cancel", {}).status_code == 200
    refused = send(client, body("task.create", {"title": "Over cap"}, request_id="over-cap"))
    assert refused.status_code == 429 and refused.json()["error"]["code"] == "AUTO_RATE_LIMIT"
    runs = app.state.services.list("run", _principal())["items"]
    assert len(runs) == 20
    # Explicit human review remains available when autonomous admission is exhausted.
    raised = send(client, body("task.create", {"title": "Ask instead"}, True, "cap-ask"))
    assert raised.status_code == 201 and raised.json()["status"] == "BLOCKED"
    # Age only these synthetic fixture records; cancelled and failed actions were charged at admission.
    with app.state.services.store.transaction() as connection:
        for run in runs:
            app.state.services._save(
                connection,
                {**run, "created_at": (datetime.now(UTC) - timedelta(seconds=601)).isoformat()},
            )
    assert (
        send(
            client, body("task.create", {"title": "Next window"}, request_id="next-window")
        ).status_code
        == 201
    )


def test_status_and_health_disclose_unverified_report_and_window(client, app):
    value = body("task.create", {"title": "Synthetic health fixture"})
    value["result"]["test_double"] = False
    run = send(client, value).json()
    assert Worker(app.state.services).tick() == 1
    report = post(
        client,
        "/runtime/health",
        {
            "run_id": run["id"],
            "request_id": "policy-test",
            "evidence_reference": "synthetic-test-only",
            "evidence_sha256": "a" * 64,
        },
        headers=AGENT,
    )
    assert report.status_code == 200, report.text
    for record in [
        report.json(),
        client.get("/api/v1/agent/status", headers=AUTH).json(),
        client.get("/health/ready", headers=AUTH).json(),
    ]:
        assert record["runtime_evidence"] == "RUNTIME_REPORTED_NOT_VERIFIED_BY_AXIOM"
        assert record["runtime_verified_by_axiom"] is False
        assert record["heartbeat_window_seconds"] == 120
    status = client.get("/api/v1/agent/status", headers=AUTH).json()
    assert status["status"] == "DEGRADED" and "not verified by AXIOM" in status["reason"]
    caps = client.get("/api/v1/system/capabilities", headers=AUTH).json()
    assert caps["agent"] == "DEGRADED"
    assert caps["agent_evidence"] == "RUNTIME_REPORTED_NOT_VERIFIED_BY_AXIOM"
    assert caps["agent_heartbeat_window_seconds"] == "120"


@pytest.mark.parametrize("relative", ["backend/app/tick.py", "backend/tests/test_process.py"])
def test_requested_format_only_files_preserve_v4_ast(relative):
    root = Path(__file__).resolve().parents[2]
    old = root.parents[1] / "g1-v4-001/AXIOM" / relative
    assert ast.dump(ast.parse((root / relative).read_text())) == ast.dump(
        ast.parse(old.read_text())
    )
