"""Human-attested bootstrap, separate from ordinary 120-second runtime health."""

from datetime import datetime, timedelta

from sqlalchemy import insert, select

from backend.app import gate
from backend.app.base_services import canonical, digest
from backend.app.gate import Services as GateServices
from backend.app.gate import human
from backend.app.gate_state import bootstrap_superseded
from backend.app.persistence import replays
from backend.app.security import Fault, require


class Services(GateServices):
    def effective_health(self, c, p):
        session = p.session_id if p.actor_kind == "agent" else "synthetic-agent-bakery"
        real = [r for r in self._list(c, "runtime_health", p) if r.get("session_id") == session]
        valid = [r for r in real if datetime.fromisoformat(r["expires_at"]) > gate.clock()]
        candidates = valid or real
        latest = max(candidates, key=lambda r: (r["created_at"], r["id"])) if candidates else None
        boots = [r for r in self._list(c, "runtime_bootstrap", p) if r["session_id"] == session]
        for b in boots:
            later = next((r for r in real if r["created_at"] >= b["created_at"]), None)
            prior = c.execute(
                select(bootstrap_superseded.c.bootstrap_id).where(
                    bootstrap_superseded.c.bootstrap_id == b["id"]
                )
            ).first()
            if later and not prior:
                c.execute(
                    insert(bootstrap_superseded).values(
                        bootstrap_id=b["id"], health_id=later["id"], at=gate.stamp()
                    )
                )
        if latest and datetime.fromisoformat(latest["expires_at"]) > gate.clock():
            return "RUNTIME_REPORTED", latest
        active = [
            b
            for b in boots
            if datetime.fromisoformat(b["expires_at"]) > gate.clock()
            and not c.execute(
                select(bootstrap_superseded.c.bootstrap_id).where(
                    bootstrap_superseded.c.bootstrap_id == b["id"]
                )
            ).first()
        ]
        if active:
            return "HUMAN_ATTESTED_BOOTSTRAP", max(active, key=lambda r: r["created_at"])
        return "NONE", latest or (max(boots, key=lambda r: r["created_at"]) if boots else None)

    def fresh_health(self, c, p):
        source, evidence = self.effective_health(c, p)
        if source == "NONE":
            self.append_fact(
                c,
                p,
                "heartbeat_lapsed",
                "RUNTIME_STALE",
                event_key=digest(
                    [
                        p.id,
                        str(p.domain),
                        gate.target(p),
                        evidence["id"] if evidence else "no-health",
                    ]
                ),
            )
        return source != "NONE"

    def runtime_status(self, p):
        value = super().runtime_status(p)
        with self.store.transaction() as c:
            source, evidence = self.effective_health(c, p)
        value["evidence_source"] = source
        if source == "HUMAN_ATTESTED_BOOTSTRAP":
            value.update(
                status="DEGRADED",
                reason="Human-attested bootstrap; not runtime-reported or verified by AXIOM",
                runtime_evidence="HUMAN_ATTESTED",
            )
            value["evidence"] = (
                []
                if p.actor_kind == "guard"
                else [{k: evidence[k] for k in ("id", "source", "expires_at", "human_principal")}]
            )
        return value

    def bootstrap(self, p, body, key=None):
        human(p)
        require(p, "run.write")
        if key and (len(key) > 128 or not key.isascii() or any(ord(c) < 33 for c in key)):
            raise Fault(400, "IDEMPOTENCY_REQUIRED", "A printable Idempotency-Key is required")
        scope = canonical([p.id, str(p.domain), "bootstrap", key]) if key else None
        with self.store.transaction() as c:
            source, _ = self.effective_health(c, p)
            if source == "RUNTIME_REPORTED":
                raise Fault(
                    409, "RUNTIME_HEALTH_ALREADY_FRESH", "Fresh real runtime health already exists"
                )
            if scope:
                prior = (
                    c.execute(select(replays).where(replays.c.scope == scope)).mappings().first()
                )
                if prior:
                    if prior["request_hash"] != digest(body):
                        raise Fault(409, "IDEMPOTENCY_CONFLICT", "Key belongs to different content")
                    return prior["body"]
            if source == "HUMAN_ATTESTED_BOOTSTRAP":
                raise Fault(
                    409,
                    "BOOTSTRAP_ALREADY_ACTIVE",
                    "At most one bootstrap may be active per session",
                )
            value = self._create(
                c,
                "runtime_bootstrap",
                p,
                {
                    **body,
                    "session_id": "synthetic-agent-bakery",
                    "source": "HUMAN_ATTESTED_BOOTSTRAP",
                    "runtime_evidence": "HUMAN_ATTESTED",
                    "runtime_verified_by_axiom": False,
                    "human_principal": p.id,
                    "expires_at": (gate.clock() + timedelta(minutes=body["minutes"])).isoformat(),
                },
            )
            receipt = self._receipt(c, p, "runtime.bootstrap", value, key or value["id"])
            self._save(c, {**receipt, "note": body["note"], "human_principal": p.id})
            self.append_fact(c, p, "bootstrap_attested", "HUMAN_ATTESTED", reference=receipt["id"])
            result = {
                "id": value["id"],
                "expires_at": value["expires_at"],
                "evidence_source": value["source"],
                "runtime_evidence": "HUMAN_ATTESTED",
                "runtime_verified_by_axiom": False,
                "receipt_id": receipt["id"],
            }
            if scope:
                c.execute(
                    insert(replays).values(scope=scope, request_hash=digest(body), body=result)
                )
            return result
