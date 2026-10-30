"""Finite, explicitly invoked durable worker. No external service is activated."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.app.domain import Domain
from backend.app.persistence import records
from backend.app.security import SCOPES, Fault, Principal


class Worker:
    def __init__(self, services):
        self.services = services

    def tick(self, at=None):
        at = at or datetime.now(UTC)
        services = self.services
        completed = 0
        # SQLite BEGIN IMMEDIATE serializes competing ticks and HTTP mutations.
        # Internal DB effects, postcondition, run status and receipt commit atomically.
        # No network/external tool executes while this lock is held.
        with services.store.transaction() as connection:
            schedules = list(
                connection.execute(
                    select(records.c.body).where(records.c.kind == "schedule")
                ).scalars()
            )
            for schedule in schedules:
                due = datetime.fromisoformat(schedule["next_due_at"])
                if due > at:
                    continue
                principal = Principal(schedule["owner_id"], Domain(schedule["domain"]), SCOPES)
                services.new_run(
                    connection, principal, "task.create", {"title": schedule["task_title"]}
                )
                # Coalesce missed periods: one task, then advance beyond the tick time.
                missed = int((at - due).total_seconds()) // schedule["interval_seconds"] + 1
                services._save(
                    connection,
                    {
                        **schedule,
                        "next_due_at": (
                            due + timedelta(seconds=missed * schedule["interval_seconds"])
                        ).isoformat(),
                    },
                )
            runs = list(
                connection.execute(select(records.c.body).where(records.c.kind == "run")).scalars()
            )
            for run in runs:
                if run["tool"].startswith("bakeoff."):
                    continue  # External network effects use the separate durable attempt boundary.
                if (
                    run["status"] == "RUNNING"
                    and run["lease_until"]
                    and datetime.fromisoformat(run["lease_until"]) <= at
                ):
                    run = services._save(
                        connection, {**run, "status": "RETRY_PENDING", "lease_until": None}
                    )
                if (
                    run["status"] not in {"QUEUED", "RETRY_PENDING"}
                    or datetime.fromisoformat(run["next_attempt_at"]) > at
                ):
                    continue
                principal = Principal(
                    run["owner_id"],
                    Domain(run["domain"]),
                    frozenset(run["execution_scopes"]),
                    actor_kind="application",
                )
                key = "run:" + run["id"]
                if run["attempts"] >= run["max_attempts"]:
                    failed = services._save(
                        connection,
                        {
                            **run,
                            "status": "FAILED",
                            "error_code": "RETRY_EXHAUSTED",
                            "lease_until": None,
                        },
                    )
                    services._receipt(
                        connection,
                        principal,
                        "run.execute",
                        failed,
                        key,
                        outcome="FAILED",
                        error="RETRY_EXHAUSTED",
                    )
                    continue
                running = services._save(
                    connection,
                    {
                        **run,
                        "status": "RUNNING",
                        "attempts": run["attempts"] + 1,
                        "lease_until": (at + timedelta(seconds=30)).isoformat(),
                    },
                )
                try:
                    context = run.get("runtime_context")
                    if context and datetime.fromisoformat(context["expires_at"]) <= at:
                        raise Fault(
                            403,
                            "EXPIRED_AUTHORITY",
                            "Bounded runtime authority expired before execution",
                        )
                    arguments = services.validate_tool(principal, run["tool"], run["arguments"])
                    # A savepoint prevents a failed tool from leaving partial state.
                    with connection.begin_nested():
                        task = services.execute_tool(connection, principal, run, arguments)
                        done = services._save(
                            connection,
                            {
                                **running,
                                "status": "COMPLETED",
                                "lease_until": None,
                                "checkpoint": {
                                    "resource_id": task["id"],
                                    "postcondition_verified": True,
                                },
                            },
                        )
                        services._receipt(connection, principal, "run.execute", done, key)
                    completed += 1
                except Fault as error:
                    failed = services._save(
                        connection,
                        {
                            **running,
                            "status": "FAILED",
                            "lease_until": None,
                            "error_code": error.code,
                        },
                    )
                    services._receipt(
                        connection,
                        principal,
                        "run.execute",
                        failed,
                        key,
                        outcome="FAILED",
                        error=error.code,
                    )
                except Exception:
                    # Do not persist exception text: it may contain private inputs.
                    services._save(
                        connection,
                        {
                            **running,
                            "status": "RETRY_PENDING",
                            "lease_until": None,
                            "next_attempt_at": (
                                at + timedelta(seconds=2 ** running["attempts"])
                            ).isoformat(),
                            "error_code": "INTERNAL_RETRY",
                        },
                    )
        services.flush_journal()
        return completed
