"""Policy-checked domain operations with atomic state, receipt and replay records."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from sqlalchemy import delete, insert, select, update

from backend.app.domain import BatchCreate, Domain, TaskCreate
from backend.app.persistence import Store, records, replays
from backend.app.security import SCOPES, Fault, Principal, require


def now():
    return datetime.now(UTC).isoformat()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


class Services:
    def __init__(self, settings):
        self.settings = settings
        self.store = Store(settings)

    def _create(self, connection, kind, principal, payload):
        timestamp = now()
        body = {
            **payload,
            "id": str(uuid4()),
            "owner_id": principal.id,
            "domain": str(principal.domain),
            "synthetic": principal.synthetic,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
        connection.execute(
            insert(records).values(
                id=body["id"],
                kind=kind,
                owner_id=principal.id,
                domain=str(principal.domain),
                body=body,
                version=1,
            )
        )
        return body

    def _get(self, connection, kind, record_id, principal):
        body = connection.execute(
            select(records.c.body).where(
                records.c.id == record_id,
                records.c.kind == kind,
                records.c.owner_id == principal.id,
                records.c.domain == str(principal.domain),
            )
        ).scalar_one_or_none()
        if body is None:
            raise Fault(404, "NOT_FOUND", "Record was not found in this session's scope")
        return body

    def _list(self, connection, kind, principal):
        return list(
            connection.execute(
                select(records.c.body)
                .where(
                    records.c.kind == kind,
                    records.c.owner_id == principal.id,
                    records.c.domain == str(principal.domain),
                )
                .order_by(records.c.id)
            ).scalars()
        )

    def _save(self, connection, body):
        body = {**body, "updated_at": now()}
        connection.execute(
            update(records)
            .where(records.c.id == body["id"])
            .values(body=body, version=records.c.version + 1)
        )
        return body

    def _receipt(self, connection, principal, action, body, key, outcome="COMPLETED", error=None):
        return self._create(
            connection,
            "receipt",
            principal,
            {
                "actor_id": principal.id
                if principal.actor_kind == "human"
                else (
                    "darla-local-worker"
                    if principal.actor_kind == "application"
                    else (principal.actor_id or "synthetic-agent")
                ),
                "action": action,
                "resource_id": body["id"],
                "outcome": outcome,
                "idempotency_key": key,
                "authority": "explicit_synthetic_session_scope",
                "evidence_hash": digest(body),
                "error_code": error,
            },
        )

    def get(self, kind, record_id, principal):
        require(principal, "read")
        with self.store.engine.connect() as connection:
            return self._get(connection, kind, record_id, principal)

    def list(self, kind, principal, limit=50, offset=0, status=None):
        require(principal, "read")
        with self.store.engine.connect() as connection:
            rows = self._list(connection, kind, principal)
        if status is not None:
            rows = [row for row in rows if row.get("status") == status]
        return {
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "limit": limit,
            "offset": offset,
        }

    def mutate(self, principal, operation, key, payload, record_id=None):
        if not key or len(key) > 128 or not key.isascii() or any(ord(c) < 33 for c in key):
            raise Fault(
                400,
                "IDEMPOTENCY_REQUIRED",
                "Provide a printable Idempotency-Key of 1â€“128 characters",
            )
        permission = {
            "task.create": "task.write",
            "task.patch": "task.write",
            "plan.create": "plan.write",
            "batch.create": "plan.write",
            "draft.create": "draft.write",
            "run.create": "run.write",
            "run.cancel": "run.write",
            "schedule.create": "schedule.write",
            "approval.decision": "approval.decide",
            "memory.create": "memory.write",
            "memory.patch": "memory.write",
            "memory.delete": "memory.write",
        }[operation]
        require(
            principal,
            permission,
            bakery=operation in {"plan.create", "batch.create", "draft.create"},
        )
        if (
            operation in {"approval.decision", "memory.patch", "memory.delete"}
            and principal.actor_kind != "human"
        ):
            raise Fault(403, "HUMAN_REQUIRED", "Only the authenticated human principal can decide")
        scope = canonical(
            [principal.id, str(principal.domain), principal.actor_kind, operation, record_id, key]
        )
        request_hash = digest(payload)
        with self.store.transaction() as connection:
            existing = (
                connection.execute(select(replays).where(replays.c.scope == scope))
                .mappings()
                .first()
            )
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise Fault(
                        409, "IDEMPOTENCY_CONFLICT", "This key belongs to a different request"
                    )
                return existing["body"]
            result = self._apply(connection, principal, operation, payload, record_id)
            self._receipt(connection, principal, operation, result, key)
            # Do not retain deleted/corrected memory values in the replay store.
            replay_body = result
            connection.execute(
                insert(replays).values(scope=scope, request_hash=request_hash, body=replay_body)
            )
        return result

    def _apply(self, connection, principal, operation, payload, record_id):
        if operation == "task.create":
            validated = TaskCreate.model_validate(payload).model_dump(mode="json")
            return self._create(connection, "task", principal, {**validated, "status": "TODO"})
        if operation == "task.patch":
            body = self._get(connection, "task", record_id, principal)
            transitions = {
                "TODO": {"IN_PROGRESS", "CANCELLED"},
                "IN_PROGRESS": {"COMPLETED", "CANCELLED"},
                "COMPLETED": set(),
                "CANCELLED": set(),
            }
            if payload["status"] not in transitions[body["status"]]:
                raise Fault(409, "INVALID_TRANSITION", "Task transition is not permitted")
            return self._save(connection, {**body, "status": payload["status"]})
        if operation == "plan.create":
            capacity = payload["capacity_minutes"]
            flag = (
                "UNKNOWN"
                if capacity is None
                else (
                    "OVER_CAPACITY" if payload["minutes_required"] > capacity else "WITHIN_CAPACITY"
                )
            )
            return self._create(
                connection,
                "plan",
                principal,
                {**payload, "status": "PROPOSED", "capacity_status": flag},
            )
        if operation == "batch.create":
            validated = BatchCreate.model_validate(payload)
            ratio = Decimal(validated.target_yield) / Decimal(validated.base_yield)
            ingredients = [
                {
                    "name": x.name,
                    "grams": str(
                        (x.grams * ratio).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
                    ),
                }
                for x in validated.ingredients
            ]
            return self._create(
                connection,
                "batch",
                principal,
                {
                    **payload,
                    "ingredients": ingredients,
                    "status": "PROPOSED",
                    "integration_status": "LOCAL_CALCULATION_NOT_SCALED_BAKING",
                },
            )
        if operation == "draft.create":
            return self._create(
                connection,
                "order",
                principal,
                {
                    **payload,
                    "status": "DRAFT",
                    "price_status": "BANK_REVIEW"
                    if payload["estimated_total_cents"] is None
                    else "UNVERIFIED_ESTIMATE",
                },
            )
        if operation == "run.create":
            self.validate_tool(principal, payload["tool"], payload["arguments"])
            return self.new_run(
                connection,
                principal,
                payload["tool"],
                payload["arguments"],
                payload.get("runtime_context"),
            )
        if operation == "run.cancel":
            body = self._get(connection, "run", record_id, principal)
            if body["status"] == "CANCELLED":
                return body
            if body["status"] in {"COMPLETED", "FAILED", "UNRESOLVED"}:
                raise Fault(409, "TERMINAL_RUN", "A terminal run cannot be cancelled")
            return self._save(connection, {**body, "status": "CANCELLED", "lease_until": None})
        if operation == "schedule.create":
            require(principal, "task.write")
            return self._create(
                connection,
                "schedule",
                principal,
                {**payload, "next_due_at": now(), "worker_operating": False},
            )
        if operation == "approval.decision":
            body = self._get(connection, "approval", record_id, principal)
            if body["status"] != "PENDING" or datetime.fromisoformat(
                body["expires_at"]
            ) <= datetime.now(UTC):
                raise Fault(409, "APPROVAL_NOT_PENDING", "Approval is expired or already decided")
            return self._save(
                connection, {**body, "status": payload["decision"], "execution_authorized": False}
            )
        if operation == "memory.create":
            body = self._create(
                connection,
                "memory",
                principal,
                {"value": payload["value"], "provenance": "user_supplied_opt_in"},
            )
            return {"id": body["id"], "saved": True, "provenance": "user_supplied_opt_in"}
        if operation == "memory.patch":
            body = self._get(connection, "memory", record_id, principal)
            self._save(
                connection, {**body, "value": payload["value"], "provenance": "user_correction"}
            )
            return {"id": body["id"], "corrected": True, "provenance": "user_correction"}
        if operation == "memory.delete":
            body = self._get(connection, "memory", record_id, principal)
            connection.execute(delete(records).where(records.c.id == record_id))
            return {"id": body["id"], "deleted": True}
        raise Fault(400, "UNKNOWN_OPERATION", "Operation is not registered")

    def validate_tool(self, principal, tool, arguments):
        if tool != "task.create":
            raise Fault(
                403,
                "TOOL_DISABLED",
                "Only the registered internal task.create tool is enabled; external effects remain disabled",
            )
        require(principal, "task.write")
        try:
            return TaskCreate.model_validate(arguments).model_dump(mode="json")
        except ValueError as error:
            raise Fault(
                422, "INVALID_TOOL_ARGUMENTS", "Tool arguments do not match TaskCreate"
            ) from error

    def new_run(self, connection, principal, tool, arguments, runtime_context=None):
        return self._create(
            connection,
            "run",
            principal,
            {
                "tool": tool,
                "arguments": arguments,
                "status": "QUEUED",
                "checkpoint": {},
                "attempts": 0,
                "max_attempts": 3,
                "next_attempt_at": now(),
                "lease_until": None,
                "error_code": None,
                "execution_scopes": sorted(principal.scopes),
                "runtime_context": runtime_context,
            },
        )

    def weekly_goal(self, principal):
        require(principal, "read", bakery=True)
        with self.store.engine.connect() as connection:
            orders = self._list(connection, "order", principal)
        return {
            "target_cents": 50000,
            "currency": "USD",
            "actual_cents": None,
            "planned_draft_cents": sum(row.get("estimated_total_cents") or 0 for row in orders),
            "source_status": "MISSING_SOURCE",
            "target_provenance": "LDR_mission_500_USD_weekly",
            "gross_sales_note": "No verified sales feed. Draft estimates are excluded from actual gross sales; fees, costs, margin and profit remain unknown.",
        }

    def seed_synthetic_memory(self, owner, domain, value, provenance):
        if not self.settings.synthetic_mode:
            raise Fault(403, "FIXTURES_DISABLED", "Synthetic fixtures are disabled")
        principal = Principal(owner, Domain(domain), SCOPES)
        with self.store.transaction() as connection:
            return self._create(
                connection, "memory", principal, {"value": value, "provenance": provenance}
            )

    def seed_synthetic_approval(self, owner, domain, purpose):
        if not self.settings.synthetic_mode:
            raise Fault(403, "FIXTURES_DISABLED", "Synthetic fixtures are disabled")
        principal = Principal(owner, Domain(domain), SCOPES)
        with self.store.transaction() as connection:
            return self._create(
                connection,
                "approval",
                principal,
                {
                    "purpose": purpose,
                    "status": "PENDING",
                    "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                    "execution_authorized": False,
                },
            )

    def runtime_event(self, principal, event, key):
        require(principal, "runtime.propose")
        if principal.actor_kind != "agent":
            raise Fault(
                403,
                "AGENT_SESSION_REQUIRED",
                "Runtime events require the authenticated agent session",
            )
        if not key or len(key) > 128:
            raise Fault(400, "IDEMPOTENCY_REQUIRED", "Runtime events require Idempotency-Key")
        payload = event.model_dump(mode="json")
        scope = canonical([principal.id, str(principal.domain), "runtime.event", event.event_id])
        request_hash = digest(payload)
        with self.store.transaction() as connection:
            existing = (
                connection.execute(select(replays).where(replays.c.scope == scope))
                .mappings()
                .first()
            )
            if existing is not None:
                if existing["request_hash"] != request_hash:
                    raise Fault(
                        409, "IDEMPOTENCY_CONFLICT", "Event ID belongs to different content"
                    )
                return existing["body"]
            run = self._get(connection, "run", event.run_id, principal)
            context = run.get("runtime_context")
            if (
                not context
                or context["request_id"] != event.request_id
                or context["agent_id"] != principal.actor_id
                or context["session_id"] != principal.session_id
            ):
                raise Fault(
                    403, "RUNTIME_EVENT_MISMATCH", "Event does not match this session's bounded run"
                )
            if datetime.fromisoformat(context["expires_at"]) <= datetime.now(UTC):
                raise Fault(403, "EXPIRED_AUTHORITY", "Runtime event authority expired")
            if run["status"] not in {"QUEUED", "RUNNING", "RETRY_PENDING"}:
                raise Fault(
                    409,
                    "RUN_NOT_MUTABLE",
                    "Run cannot accept runtime progress in its current state",
                )
            if event.kind == "CHECKPOINT":
                if not event.checkpoint_reference:
                    raise Fault(
                        422,
                        "CHECKPOINT_REFERENCE_REQUIRED",
                        "Supply a bounded checkpoint reference",
                    )
                body = {
                    **run,
                    "checkpoint": {
                        **run["checkpoint"],
                        "runtime_reference": event.checkpoint_reference,
                        "runtime_event_id": event.event_id,
                    },
                }
            else:
                if event.error_classification not in {
                    "TIMEOUT",
                    "UNAVAILABLE",
                    "INVALID_OUTPUT",
                    "DENIED",
                }:
                    raise Fault(
                        422, "ERROR_CLASS_REQUIRED", "Supply a typed runtime error classification"
                    )
                body = {
                    **run,
                    "status": "BLOCKED",
                    "error_code": event.error_classification,
                    "lease_until": None,
                }
            result = self._save(connection, body)
            self._receipt(
                connection,
                principal,
                "runtime.event.recorded",
                result,
                "runtime-event:" + event.event_id,
            )
            connection.execute(
                insert(replays).values(scope=scope, request_hash=request_hash, body=result)
            )
        return result

    def flush_journal(self):
        """Export immutable redacted receipts. DB is the transactional source of truth.

        A journal write failure leaves the DB receipt intact and raises; callers can retry.
        Existing files must match exactly, preventing silent overwrite/tampering.
        """
        with self.store.engine.connect() as connection:
            receipts = list(
                connection.execute(
                    select(records.c.body).where(records.c.kind == "receipt")
                ).scalars()
            )
        for receipt in receipts:
            envelope = {
                "schema_version": "darla-receipt/1",
                "receipt_id": receipt["id"],
                "receipt": receipt,
                "sha256": digest(receipt),
            }
            data = canonical(envelope).encode()
            path = self.settings.path("receipts/" + receipt["id"] + ".json")
            try:
                with path.open("xb") as output:
                    output.write(data)
                    output.flush()
                    import os

                    os.fsync(output.fileno())
            except FileExistsError:
                if path.read_bytes() != data:
                    raise Fault(
                        503,
                        "JOURNAL_INTEGRITY",
                        "Existing receipt export differs from durable state",
                    ) from None
