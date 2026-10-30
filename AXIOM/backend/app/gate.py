"""Fail-closed proposal gate, anchors, audit-derived feed."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import insert, select, update

from backend.app.base_services import canonical, digest
from backend.app.gate_state import anchors, audit, control
from backend.app.loop import Services as LoopServices
from backend.app.persistence import replays
from backend.app.security import Fault, require

FEED_TYPES = (
    "proposal_refused",
    "heartbeat_lapsed",
    "agent_locked",
    "agent_rearmed",
    "bootstrap_attested",
)
ADMISSIONS = {
    "/api/v1/runtime/proposals",
    "/api/v1/runs",
    "/api/v1/tasks",
    "/api/v1/bakery/plans",
    "/api/v1/bakery/batch-plans",
    "/api/v1/orders/drafts",
}


def clock():
    return datetime.now(UTC)


def stamp():
    return clock().isoformat(timespec="milliseconds")


def target(p):
    return p.actor_id if p.actor_kind == "agent" else "synthetic-darla-agent"


def human(p):
    require(p, "read")
    if p.actor_kind != "human":
        raise Fault(403, "HUMAN_REQUIRED", "Only a human may perform this operation")


def reader(p):
    if p.actor_kind == "guard" and "guard" in p.scopes:
        return
    human(p)


class Services(LoopServices):
    def control_where(self, p):
        return (
            (control.c.owner_id == p.id)
            & (control.c.domain == str(p.domain))
            & (control.c.agent_id == target(p))
        )

    def locked(self, c, p):
        return bool(
            c.execute(select(control.c.locked).where(self.control_where(p))).scalar_one_or_none()
        )

    def append_fact(
        self, c, p, kind, code, tool=None, reason=None, note=None, reference=None, event_key=None
    ):
        if (
            event_key
            and c.execute(select(audit.c.id).where(audit.c.event_key == event_key)).first()
        ):
            return None
        return c.execute(
            insert(audit).values(
                at=stamp(),
                owner_id=p.id,
                domain=str(p.domain),
                type=kind,
                code=code,
                agent_id=target(p),
                tool=tool,
                caller=p.actor_id or p.id,
                reason=reason,
                note=note,
                reference_id=reference,
                event_key=event_key,
            )
        ).inserted_primary_key[0]

    def fresh_health(self, c, p):
        rows = [
            r
            for r in self._list(c, "runtime_health", p)
            if r.get("session_id")
            == (p.session_id if p.actor_kind == "agent" else "synthetic-agent-bakery")
        ]
        latest = max(rows, key=lambda r: (r["expires_at"], r["id"])) if rows else None
        fresh = latest is not None and datetime.fromisoformat(latest["expires_at"]) > clock()
        if not fresh:
            # Observation is durable once per expired evidence generation, not a background timer.
            self.append_fact(
                c,
                p,
                "heartbeat_lapsed",
                "RUNTIME_STALE",
                event_key=digest(
                    [p.id, str(p.domain), target(p), latest["id"] if latest else "no-health"]
                ),
            )
        return fresh

    def check_admission(self, c, p):
        if p.actor_kind != "agent":
            return
        fresh = self.fresh_health(c, p)
        if self.locked(c, p):
            raise Fault(
                423, "AGENT_LOCKED", "Agent proposals remain locked until a human re-arms them"
            )
        if not fresh:
            raise Fault(
                423, "RUNTIME_STALE", "A bound runtime health report within 120 seconds is required"
            )

    def mutate(self, principal, operation, key, payload, record_id=None):
        with self.store.transaction() as c:
            if principal.actor_kind == "agent" and operation in {
                "run.create",
                "task.create",
                "plan.create",
                "batch.create",
                "draft.create",
            }:
                self.check_admission(c, principal)
            return super().mutate(principal, operation, key, payload, record_id)

    def runtime_status(self, p):
        require(p, "guard" if p.actor_kind == "guard" else "read")
        with self.store.transaction() as c:
            fresh = self.fresh_health(c, p)
            locked = self.locked(c, p)
            if p.actor_kind == "guard":
                # Same published status envelope, without tool/health-report bodies.
                value = {
                    "status": "DEGRADED" if fresh else "UNAVAILABLE",
                    "contract_version": "darla-runtime/1",
                    "reason": "Runtime-reported, not verified by AXIOM; 120-second heartbeat window",
                    "evidence": [],
                    "runtime_evidence": "RUNTIME_REPORTED_NOT_VERIFIED_BY_AXIOM",
                    "runtime_verified_by_axiom": False,
                    "heartbeat_window_seconds": 120,
                }
            else:
                value = super().runtime_status(p)
        return {**value, "locked": locked}

    def control_change(self, p, kind, text, key=None):
        if kind == "lock":
            if p.actor_kind == "guard":
                require(p, "guard")
            else:
                human(p)
        else:
            human(p)
        # Agent lock is rejected even when it has the ordinary read scope.
        if p.actor_kind == "agent":
            raise Fault(403, "HUMAN_OR_GUARD_REQUIRED", "An agent cannot control its own lock")
        if key and (len(key) > 128 or not key.isascii() or any(ord(c) < 33 for c in key)):
            raise Fault(
                400,
                "IDEMPOTENCY_REQUIRED",
                "Idempotency-Key must be printable and at most 128 characters",
            )
        scope = (
            canonical([p.id, str(p.domain), p.actor_kind, "control", kind, key]) if key else None
        )
        payload_hash = digest(text)
        with self.store.transaction() as c:
            if scope:
                prior = (
                    c.execute(select(replays).where(replays.c.scope == scope)).mappings().first()
                )
                if prior:
                    if prior["request_hash"] != payload_hash:
                        raise Fault(409, "IDEMPOTENCY_CONFLICT", "Key belongs to different content")
                    return prior["body"]
            existing = c.execute(select(control).where(self.control_where(p))).mappings().first()
            wanted = kind == "lock"
            changed = bool(existing["locked"]) != wanted if existing else wanted
            values = {
                "owner_id": p.id,
                "domain": str(p.domain),
                "agent_id": target(p),
                "locked": wanted,
                "updated_at": stamp(),
            }
            if existing:
                c.execute(update(control).where(self.control_where(p)).values(**values))
            else:
                c.execute(insert(control).values(**values))
            result = {"locked": wanted, "agent_id": target(p), "changed": changed}
            if kind == "lock":
                self.append_fact(
                    c,
                    p,
                    "agent_locked" if changed else "lock_repeated",
                    "AGENT_LOCKED",
                    reason=text,
                )
            else:
                decision = self._create(
                    c,
                    "runtime_rearm",
                    p,
                    {"note": text, "human_principal": p.id, "agent_id": target(p)},
                )
                receipt = self._receipt(c, p, "runtime.rearm", decision, key or decision["id"])
                receipt = self._save(c, {**receipt, "note": text, "human_principal": p.id})
                self.append_fact(
                    c, p, "agent_rearmed", "AGENT_REARMED", note=text, reference=receipt["id"]
                )
                result.update(
                    receipt_id=receipt["id"],
                    decision_id=decision["id"],
                    note=text,
                    human_principal=p.id,
                )
            if scope:
                c.execute(
                    insert(replays).values(scope=scope, request_hash=payload_hash, body=result)
                )
            return result

    def anchor_append(self, p, body):
        require(p, "runtime.propose")
        if p.actor_kind != "agent":
            raise Fault(403, "AGENT_SESSION_REQUIRED", "Only the runtime agent may post anchors")
        with self.store.transaction() as c:
            previous = c.execute(
                select(anchors.c.seq)
                .where(
                    anchors.c.owner_id == p.id,
                    anchors.c.domain == str(p.domain),
                    anchors.c.journal_id == body["journal_id"],
                )
                .order_by(anchors.c.seq.desc())
                .limit(1)
            ).scalar_one_or_none()
            if previous is not None and body["seq"] < previous:
                self.append_fact(c, p, "anchor_refused", "ANCHOR_SEQUENCE_REGRESSION")
                # Endpoint preserves this refusal fact in the outer transaction.
                raise Fault(
                    409,
                    "ANCHOR_SEQUENCE_REGRESSION",
                    "Sequence cannot be lower than the stored maximum",
                )
            value = {
                "id": str(uuid4()),
                "server_time": stamp(),
                "owner_id": p.id,
                "domain": str(p.domain),
                "agent_id": target(p),
                **body,
            }
            c.execute(insert(anchors).values(**value))
            self.append_fact(c, p, "anchor_stored", "ANCHOR_STORED", reference=value["id"])
            return {"id": value["id"], "server_time": value["server_time"]}

    def anchor_list(self, p, journal_id, since, after, limit):
        human(p)
        q = (
            select(anchors)
            .where(
                anchors.c.owner_id == p.id,
                anchors.c.domain == str(p.domain),
                anchors.c.order_id > after,
            )
            .order_by(anchors.c.order_id)
            .limit(limit)
        )
        if journal_id:
            q = q.where(anchors.c.journal_id == journal_id)
        if since:
            q = q.where(anchors.c.server_time >= since)
        with self.store.transaction() as c:
            rows = [dict(r) for r in c.execute(q).mappings()]
        return {
            "items": rows,
            "next_after": rows[-1]["order_id"] if rows else after,
            "limit": limit,
        }

    def event_feed(self, p, since, limit):
        reader(p)
        with self.store.transaction() as c:
            self.fresh_health(c, p)
            q = (
                select(
                    audit.c.id,
                    audit.c.at,
                    audit.c.type,
                    audit.c.code,
                    audit.c.agent_id,
                    audit.c.tool,
                )
                .where(
                    audit.c.owner_id == p.id,
                    audit.c.domain == str(p.domain),
                    audit.c.id > since,
                    audit.c.type.in_(FEED_TYPES),
                )
                .order_by(audit.c.id)
                .limit(limit)
            )
            rows = [dict(r) for r in c.execute(q).mappings()]
        return {"items": rows, "next_since": rows[-1]["id"] if rows else since, "limit": limit}
