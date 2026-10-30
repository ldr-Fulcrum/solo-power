"""Repeatable synthetic bakery setup using only AXIOM public HTTP endpoints."""

import argparse
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

VERSION = "bakery-demo-20261029-v1"
TASKS = ("Demo: weigh flour", "Demo: prepare trays", "Demo: check oven")
PLAN = {
    "title": "Demo: morning bake",
    "minutes_required": 90,
    "capacity_minutes": 120,
    "provenance": "synthetic_demo_unverified",
}
DRAFT = {"description": "Demo: 12 bread rolls; synthetic draft only", "estimated_total_cents": 2400}
AUTO = {"tool": "task.create", "arguments": {"title": "Demo: mix dough"}}
ASK = {"tool": "draft.create", "arguments": DRAFT}
# Public nonproduction fixture constant already defined by the application.
FIXTURE = "synthetic-dani-bakery"


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DemoError(Exception):
    pass


class Api:
    def __init__(self, base_url):
        parts = urlsplit(base_url)
        if (
            parts.scheme != "http"
            or parts.hostname not in {"127.0.0.1", "localhost"}
            or parts.username
            or parts.password
            or parts.path not in {"", "/"}
            or parts.query
            or parts.fragment
        ):
            raise DemoError("Use an explicit loopback HTTP base URL without credentials or a path")
        self.base = base_url.rstrip("/")
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def request(self, path, payload=None, key=None):
        headers = {"Authorization": "Bearer " + FIXTURE, "Accept": "application/json"}
        body = None
        if payload is not None:
            body = json.dumps(payload, sort_keys=True).encode()
            headers.update(
                {"Content-Type": "application/json", "Idempotency-Key": VERSION + ":" + key}
            )
        req = Request(self.base + "/api/v1" + path, data=body, headers=headers)
        try:
            with self.opener.open(req, timeout=45) as response:
                return json.load(response)
        except HTTPError as error:
            try:
                code = json.load(error).get("error", {}).get("code", "HTTP_ERROR")
            except (ValueError, AttributeError):
                code = "HTTP_ERROR"
            raise DemoError(f"HTTP {error.code} at {path}: {code}") from None
        except (URLError, TimeoutError, ValueError):
            raise DemoError("Local API unavailable or returned invalid JSON") from None


def seed(api, approve=False):
    integration = api.request("/integrations")
    if integration.get("bakeoff") != "DISABLED":
        raise DemoError("This core-only rehearsal requires the optional task-hub adapter disabled")
    tasks = [
        api.request("/tasks", {"title": title}, f"task-{i}") for i, title in enumerate(TASKS, 1)
    ]
    plan = api.request("/bakery/plans", PLAN, "plan")
    auto_created = api.request("/runs", AUTO, "auto-run")
    ask_created = api.request("/runs", ASK, "ask-run")
    auto = api.request("/runs/" + auto_created["id"])
    ask = api.request("/runs/" + ask_created["id"])
    approval = api.request("/approvals/" + ask["approval_id"])
    if approve:
        if approval["status"] == "PENDING":
            approval = api.request(
                "/approvals/" + approval["id"] + "/decision",
                {"decision": "APPROVED"},
                "approve-demo-draft",
            )
        elif approval["status"] != "APPROVED":
            raise DemoError("The demo item was already rejected; use a fresh rehearsal state")
        ask = api.request("/runs/" + ask["id"])
    elif approval["status"] != "PENDING":
        raise DemoError("This rehearsal was already decided; the script will not reset it")
    result = {
        "scenario": VERSION,
        "mode": "SYNTHETIC_HUMAN_FIXTURE_NOT_AGENT_OR_MODEL_EVIDENCE",
        "tasks": [{"id": t["id"], "title": t["title"], "status": t["status"]} for t in tasks],
        "plan": {k: plan[k] for k in ["id", "title", "status", "capacity_status"]},
        "auto": {k: auto[k] for k in ["id", "tool", "status", "approval", "tool_tier"]},
        "ask": {k: ask[k] for k in ["id", "tool", "status", "approval_id", "tool_tier"]},
        "approval": {k: approval[k] for k in ["id", "status", "expires_at"]},
        "effects": "Three local tasks and one plan. Runs wait for an explicit worker tick; no draft effect or external action executed.",
    }
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument(
        "--approve",
        action="store_true",
        help="Record the synthetic human approval for this demo draft only",
    )
    args = parser.parse_args()
    try:
        print(json.dumps(seed(Api(args.base_url), args.approve), indent=2))
    except DemoError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
