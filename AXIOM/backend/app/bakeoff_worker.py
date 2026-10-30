"""Finite external worker: durable intent, network outside DB, no blind retries."""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from backend.app.bakeoff_client import BakeoffFailure
from backend.app.domain import Domain
from backend.app.persistence import records
from backend.app.security import Fault, Principal
from backend.app.worker import Worker as InternalWorker


class Worker:
    def __init__(self, services):
        self.services = services

    def _finish(self, c, p, run, status, code, meta, payload=None):
        s = self.services
        checkpoint = {
            **run["checkpoint"],
            "postcondition_verified": status == "COMPLETED",
            "postcondition_scope": "ADAPTER_RECEIPT_AND_LOCAL_PERSISTENCE_ONLY",
        }
        if payload is not None:
            text = json.dumps(payload, sort_keys=True, ensure_ascii=True)
            if len(text) > 7900:
                text = text[:7900] + "\n[TRUNCATED_UNTRUSTED_DATA]"
            data = s.store_untrusted(
                c, p, "optional task-hub adapter: " + run["tool"], text, "host_bakeoff_mcp_result"
            )
            result = s._create(
                c,
                "bakeoff_result",
                p,
                {"tool": run["tool"], "untrusted_text_id": data["id"], "bakeoff": meta},
            )
            checkpoint.update(resource_id=result["id"], untrusted_text_id=data["id"])
        value = s._save(
            c,
            {
                **run,
                "status": status,
                "lease_until": None,
                "error_code": code,
                "error_message": code
                + ("; human resolution required" if status == "UNRESOLVED" else "")
                if code
                else None,
                "bakeoff": meta,
                "checkpoint": checkpoint,
            },
        )
        s._receipt(c, p, "run.execute", value, "run:" + run["id"], outcome=status, error=code)
        return value

    def tick(self, at=None, max_jobs=20):
        if not 1 <= max_jobs <= 50:
            raise ValueError("max_jobs must be 1..50")
        at = at or datetime.now(UTC)
        s = self.services
        completed = InternalWorker(s).tick(at)
        for _ in range(max_jobs):
            selected = None
            with s.store.transaction() as c:
                runs = list(
                    c.execute(select(records.c.body).where(records.c.kind == "run")).scalars()
                )
                for run in runs:
                    if not run["tool"].startswith("bakeoff."):
                        continue
                    p = Principal(
                        run["owner_id"],
                        Domain(run["domain"]),
                        frozenset(run["execution_scopes"]),
                        actor_kind="application",
                    )
                    if run["status"] == "RUNNING":
                        if run["lease_until"] and datetime.fromisoformat(run["lease_until"]) <= at:
                            meta = {
                                "tool": run["tool"],
                                "http_status": None,
                                "outcome": "UNRESOLVED",
                                "receipt_id": None,
                                "replayed": False,
                                "error_class": "BAKEOFF_INTERRUPTED_UNKNOWN",
                            }
                            self._finish(
                                c, p, run, "UNRESOLVED", "BAKEOFF_INTERRUPTED_UNKNOWN", meta
                            )
                        continue
                    if (
                        run["status"] not in {"QUEUED", "RETRY_PENDING"}
                        or datetime.fromisoformat(run["next_attempt_at"]) > at
                    ):
                        continue
                    try:
                        if run["checkpoint"].get("external_attempt_started"):
                            raise Fault(
                                409,
                                "BAKEOFF_RETRY_REFUSED",
                                "Existing external attempt cannot retry automatically",
                            )
                        context = run.get("runtime_context")
                        if context and datetime.fromisoformat(context["expires_at"]) <= at:
                            raise Fault(403, "EXPIRED_AUTHORITY", "Runtime authority expired")
                        args = s.validate_tool(p, run["tool"], run["arguments"])
                        s.authorize_execution(c, p, run)
                    except Fault as error:
                        meta = {
                            "tool": run["tool"],
                            "http_status": None,
                            "outcome": "FAILED",
                            "receipt_id": None,
                            "replayed": False,
                            "error_class": error.code,
                        }
                        self._finish(c, p, run, "FAILED", error.code, meta)
                        continue
                    running = s._save(
                        c,
                        {
                            **run,
                            "status": "RUNNING",
                            "attempts": run["attempts"] + 1,
                            "lease_until": (at + timedelta(seconds=30)).isoformat(),
                            "checkpoint": {
                                **run["checkpoint"],
                                "external_attempt_started": True,
                                "idempotency_key": "axiom:" + run["id"],
                            },
                        },
                    )
                    selected = (p, running, args)
                    break
            if selected is None:
                break
            p, run, args = selected
            payload = None
            code = None
            status = "COMPLETED"
            try:
                if not s.bakeoff:
                    raise BakeoffFailure("BAKEOFF_NOT_CONFIGURED")
                result = s.bakeoff.call(run["tool"].removeprefix("bakeoff."), args, run["id"])
                payload = result.pop("untrusted_payload")
                meta = {"tool": run["tool"], **result}
            except BakeoffFailure as error:
                code = error.code
                status = "UNRESOLVED" if error.unknown else "FAILED"
                meta = {
                    "tool": run["tool"],
                    "http_status": error.http_status,
                    "outcome": status,
                    "receipt_id": None,
                    "replayed": False,
                    "error_class": code,
                }
            except Exception:
                code = "BAKEOFF_UNKNOWN_OUTCOME"
                status = "UNRESOLVED"
                meta = {
                    "tool": run["tool"],
                    "http_status": None,
                    "outcome": status,
                    "receipt_id": None,
                    "replayed": False,
                    "error_class": code,
                }
            with s.store.transaction() as c:
                current = s._get(c, "run", run["id"], p)
                if current["status"] != "RUNNING":
                    s._receipt(
                        c,
                        p,
                        "bakeoff.late_result",
                        {**current, "bakeoff": meta},
                        "late:" + run["id"],
                        outcome="UNRESOLVED",
                        error="BAKEOFF_LATE_RESULT",
                    )
                else:
                    self._finish(c, p, current, status, code, meta, payload)
                    if status == "COMPLETED":
                        completed += 1
        s.flush_journal()
        return completed
