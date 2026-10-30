"""Candidate v4: durable application bridge. No inference or external effects."""

from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import Field
from sqlalchemy import insert, select

from backend.app import approval_policy as policy
from backend.app.base_services import Services as BaselineServices
from backend.app.base_services import canonical, digest
from backend.app.domain import BakeryPlanCreate, BatchCreate, Model, OrderDraftCreate, TaskCreate
from backend.app.persistence import replays
from backend.app.security import Fault, require

TOOLS = {
    "task.create": (TaskCreate, "task.write", "task"),
    "plan.create": (BakeryPlanCreate, "plan.write", "plan"),
    "batch.create": (BatchCreate, "plan.write", "batch"),
    "draft.create": (OrderDraftCreate, "draft.write", "order"),
}


class Claim(Model):
    message_id: str = Field(min_length=1, max_length=100)


class Reply(Claim):
    text: str = Field(min_length=1, max_length=8000)
    provenance_references: list[str] = Field(default_factory=list, max_length=30)


class SupplierText(Model):
    source: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=8000)


class HealthEvidence(Model):
    run_id: str = Field(min_length=1, max_length=100)
    request_id: str = Field(min_length=1, max_length=90)
    evidence_reference: str = Field(min_length=1, max_length=200)
    evidence_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Refusal(Model):
    source_id: str = Field(min_length=1, max_length=100)
    classification: Literal[
        "INSTRUCTION_IN_UNTRUSTED_DATA", "EXFILTRATION_REQUEST", "FORGED_APPROVAL"
    ]
    provenance_references: list[str] = Field(default_factory=list, max_length=30)


def agent(principal):
    require(principal, "runtime.propose")
    if principal.actor_kind != "agent":
        raise Fault(403, "AGENT_SESSION_REQUIRED", "An authenticated runtime session is required")


def human(principal):
    require(principal, "run.write")
    if principal.actor_kind != "human":
        raise Fault(403, "HUMAN_REQUIRED", "An authenticated human must initiate this operation")


def action_hash(run):
    return digest({k: run[k] for k in ("tool", "arguments", "runtime_context")})


class Services(BaselineServices):
    def tool_registry(self):
        return TOOLS

    def tool_tier(self, tool):
        return policy.TIERS[tool]

    def tool_policy_version(self, tool):
        return policy.VERSION

    def approval_required(self, tool, raised=False):
        return self.tool_tier(tool) == "ASK" or raised

    def runtime_tools(self, principal):
        require(principal, "read")
        return {
            name: {
                "arguments": schema.model_json_schema(),
                "scope": scope,
                "approval_required": self.approval_required(name),
                "tier": self.tool_tier(name),
                "policy_version": self.tool_policy_version(name),
                "auto_limit": policy.AUTO_LIMIT,
                "auto_window_seconds": policy.AUTO_WINDOW_SECONDS,
                "effect": "HOST_BAKEOFF" if name.startswith("bakeoff.") else "LOCAL_ONLY",
            }
            for name, (schema, scope, kind) in self.tool_registry().items()
        }

    def store_untrusted(
        self, connection, principal, source, text, provenance="human_imported_supplier_text"
    ):
        return self._create(
            connection,
            "supplier",
            principal,
            {
                "source": source,
                "text": text,
                "trust": "UNTRUSTED_DATA",
                "instruction_authority": "NONE",
                "provenance": provenance,
            },
        )

    def _receipt(self, connection, principal, action, body, key, outcome="COMPLETED", error=None):
        receipt = super()._receipt(connection, principal, action, body, key, outcome, error)
        context = body.get("runtime_context") or {}
        references = context.get("provenance_references", body.get("provenance_references", []))
        return self._save(
            connection,
            {
                **receipt,
                **{k: body[k] for k in ("approval", "policy_version", "tool_tier") if k in body},
                "provenance_references": references,
                "provenance_verification": "RUNTIME_REPORTED" if references else "NONE",
            },
        )

    def validate_tool(self, principal, tool, arguments):
        if tool not in self.tool_registry():
            raise Fault(403, "TOOL_DISABLED", "Tool is not in the local application registry")
        schema, scope, _kind = self.tool_registry()[tool]
        require(principal, scope, bakery=tool != "task.create")
        try:
            return schema.model_validate(arguments).model_dump(mode="json")
        except ValueError as error:
            raise Fault(
                422, "INVALID_TOOL_ARGUMENTS", "Arguments do not match the tool schema"
            ) from error

    def _apply(self, connection, principal, operation, payload, record_id):
        if operation == "run.create":
            tool = payload["tool"]
            self.validate_tool(principal, tool, payload["arguments"])
            needed = self.approval_required(tool, payload.get("requires_approval", False))
            if not needed and principal.actor_kind == "agent":
                cutoff = policy.clock() - timedelta(seconds=policy.AUTO_WINDOW_SECONDS)
                recent = [
                    r
                    for r in self._list(connection, "run", principal)
                    if r.get("approval") == "AUTO_BY_POLICY"
                    and (r.get("runtime_context") or {}).get("session_id") == principal.session_id
                    and (r.get("runtime_context") or {}).get("agent_id") == principal.actor_id
                    and datetime.fromisoformat(r["created_at"]) > cutoff
                ]
                if len(recent) >= policy.AUTO_LIMIT:
                    raise Fault(
                        429,
                        "AUTO_RATE_LIMIT",
                        "At most 20 AUTO actions per agent session in a rolling 600-second window",
                    )
            result = super()._apply(connection, principal, operation, payload, record_id)
            result = self._save(
                connection,
                {
                    **result,
                    "approval": "HUMAN_REQUIRED" if needed else "AUTO_BY_POLICY",
                    "policy_version": self.tool_policy_version(tool),
                    "tool_tier": self.tool_tier(tool),
                    "model_requires_approval": payload.get("requires_approval", False),
                },
            )
            if needed:
                approval = self._create(
                    connection,
                    "approval",
                    principal,
                    {
                        **(
                            {"effect": "HOST_BAKEOFF", "tool": tool}
                            if tool.startswith("bakeoff.")
                            else {}
                        ),
                        "purpose": (
                            "optional task-hub adapter: " + tool + ". "
                            if tool.startswith("bakeoff.")
                            else ""
                        )
                        + payload.get("summary", "Review local " + payload["tool"]),
                        "approval": "HUMAN_REQUIRED",
                        "policy_version": self.tool_policy_version(tool),
                        "tool_tier": self.tool_tier(tool),
                        "status": "PENDING",
                        "expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
                        "execution_authorized": False,
                        "run_id": result["id"],
                        "action_hash": action_hash(result),
                    },
                )
                result = self._save(
                    connection, {**result, "status": "BLOCKED", "approval_id": approval["id"]}
                )
                self._receipt(
                    connection,
                    principal,
                    "approval.requested",
                    approval,
                    "approval:" + result["id"],
                )
            return result
        if operation == "approval.decision":
            approval = self._get(connection, "approval", record_id, principal)
            if not approval.get("run_id"):
                return super()._apply(connection, principal, operation, payload, record_id)
            run = self._get(connection, "run", approval["run_id"], principal)
            if approval["status"] != "PENDING" or datetime.fromisoformat(
                approval["expires_at"]
            ) <= datetime.now(UTC):
                raise Fault(409, "APPROVAL_NOT_PENDING", "Approval is expired or already decided")
            if (
                run["status"] != "BLOCKED"
                or run.get("approval_id") != approval["id"]
                or action_hash(run) != approval["action_hash"]
            ):
                raise Fault(
                    409, "APPROVAL_BINDING_MISMATCH", "Approval does not bind this pending action"
                )
            context = run.get("runtime_context")
            if context and datetime.fromisoformat(context["expires_at"]) <= datetime.now(UTC):
                raise Fault(403, "EXPIRED_AUTHORITY", "The originating runtime authority expired")
            approved = payload["decision"] == "APPROVED"
            self._save(connection, {**run, "status": "QUEUED" if approved else "CANCELLED"})
            return self._save(
                connection,
                {
                    **approval,
                    "status": payload["decision"],
                    "execution_authorized": approved,
                    "decided_by": principal.id,
                },
            )
        return super()._apply(connection, principal, operation, payload, record_id)

    def authorize_execution(self, connection, principal, run):
        approval_id = run.get("approval_id")
        if (
            self.approval_required(run["tool"], run.get("model_requires_approval", False))
            and not approval_id
        ):
            raise Fault(
                403, "APPROVAL_REQUIRED", "Server policy or raised model flag requires human review"
            )
        if approval_id:
            approval = self._get(connection, "approval", approval_id, principal)
            if (
                approval["status"] != "APPROVED"
                or not approval["execution_authorized"]
                or approval["run_id"] != run["id"]
                or approval["action_hash"] != action_hash(run)
                or datetime.fromisoformat(approval["expires_at"]) <= datetime.now(UTC)
            ):
                raise Fault(
                    403,
                    "APPROVAL_NOT_BOUND",
                    "A current human decision must bind this exact action",
                )

    def execute_tool(self, connection, principal, run, arguments):
        self.authorize_execution(connection, principal, run)
        result = self._apply(connection, principal, run["tool"], arguments, None)
        stored = self._get(
            connection, self.tool_registry()[run["tool"]][2], result["id"], principal
        )
        if stored != result:
            raise Fault(500, "POSTCONDITION_FAILED", "Persisted application result differs")
        return stored

    def bridge(self, principal, operation, key, payload):
        """Receipt, effect and replay commit together. Caller keys never grant authority."""
        if not key or len(key) > 128 or not key.isascii() or any(ord(c) < 33 for c in key):
            raise Fault(400, "IDEMPOTENCY_REQUIRED", "A printable Idempotency-Key is required")
        if operation in {"message", "supplier", "cancel_message"}:
            human(principal)
        else:
            agent(principal)
        scope = canonical(
            [
                principal.id,
                str(principal.domain),
                principal.actor_kind,
                principal.session_id,
                operation,
                key,
            ]
        )
        request_hash = digest(payload)
        with self.store.transaction() as connection:
            existing = (
                connection.execute(select(replays).where(replays.c.scope == scope))
                .mappings()
                .first()
            )
            if existing:
                if existing["request_hash"] != request_hash:
                    raise Fault(409, "IDEMPOTENCY_CONFLICT", "Key belongs to different content")
                return existing["body"]
            result = self._bridge_apply(connection, principal, operation, payload)
            self._receipt(connection, principal, "bridge." + operation, result, key)
            connection.execute(
                insert(replays).values(scope=scope, request_hash=request_hash, body=result)
            )
        return result

    def _bridge_apply(self, connection, principal, operation, payload):
        if operation == "message":
            thread_id = payload.get("thread_id")
            thread = (
                self._get(connection, "thread", thread_id, principal)
                if thread_id
                else self._create(connection, "thread", principal, {"messages": []})
            )
            message = self._create(
                connection,
                "message",
                principal,
                {
                    "thread_id": thread["id"],
                    "text": payload["message"],
                    "role": "user",
                    "status": "QUEUED",
                    "claim_session": None,
                    "claim_until": None,
                },
            )
            self._save(connection, {**thread, "messages": [*thread["messages"], message]})
            return message
        if operation == "supplier":
            require(principal, "plan.write", bakery=True)
            return self.store_untrusted(connection, principal, payload["source"], payload["text"])
        if operation == "refusal":
            source = self._get(connection, "supplier", payload["source_id"], principal)
            return self._create(
                connection,
                "refusal",
                principal,
                {
                    **payload,
                    "source_sha256": digest(source),
                    "outcome": "RUNTIME_REPORTED_REFUSAL",
                    "independently_verified": False,
                },
            )
        if operation == "health":
            run = self._get(connection, "run", payload["run_id"], principal)
            context = run.get("runtime_context") or {}
            if (
                run["status"] != "COMPLETED"
                or not run["checkpoint"].get("postcondition_verified")
                or context.get("request_id") != payload["request_id"]
                or context.get("agent_id") != principal.actor_id
                or context.get("session_id") != principal.session_id
                or context.get("test_double", True)
            ):
                raise Fault(
                    403,
                    "HEALTH_EVIDENCE_NOT_BOUND",
                    "Health requires a completed non-double runtime action from this session",
                )
            return self._create(
                connection,
                "runtime_health",
                principal,
                {
                    **payload,
                    "session_id": principal.session_id,
                    "expires_at": (
                        datetime.now(UTC) + timedelta(seconds=policy.HEARTBEAT_WINDOW_SECONDS)
                    ).isoformat(),
                    "runtime_evidence": policy.RUNTIME_EVIDENCE,
                    "runtime_verified_by_axiom": False,
                    "heartbeat_window_seconds": policy.HEARTBEAT_WINDOW_SECONDS,
                    "trace_verification": "RUNTIME_REPORTED_HASH_NOT_INDEPENDENTLY_READ",
                },
            )
        message = self._get(connection, "message", payload["message_id"], principal)
        if operation == "cancel_message":
            if message["status"] in {"COMPLETED", "CANCELLED"}:
                raise Fault(409, "MESSAGE_TERMINAL", "Message is already terminal")
            return self._save(connection, {**message, "status": "CANCELLED"})
        if operation == "claim":
            if message["status"] != "QUEUED":
                raise Fault(
                    409,
                    "MESSAGE_NOT_QUEUED",
                    "Message was claimed or is terminal; no automatic inference retry",
                )
            return self._save(
                connection,
                {
                    **message,
                    "status": "CLAIMED",
                    "claim_session": principal.session_id,
                    "claim_until": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
                },
            )
        if operation == "reply":
            if (
                message["status"] != "CLAIMED"
                or message["claim_session"] != principal.session_id
                or datetime.fromisoformat(message["claim_until"]) <= datetime.now(UTC)
            ):
                raise Fault(
                    409,
                    "MESSAGE_CLAIM_INVALID",
                    "A current claim from this runtime session is required",
                )
            thread = self._get(connection, "thread", message["thread_id"], principal)
            reply = self._create(
                connection,
                "reply",
                principal,
                {
                    **payload,
                    "thread_id": thread["id"],
                    "role": "assistant",
                    "provenance_verification": "RUNTIME_REPORTED",
                },
            )
            done = self._save(
                connection, {**message, "status": "COMPLETED", "reply_id": reply["id"]}
            )
            history = [done if item["id"] == done["id"] else item for item in thread["messages"]]
            self._save(connection, {**thread, "messages": [*history, reply]})
            return reply
        raise Fault(400, "UNKNOWN_OPERATION", "Unknown bridge operation")

    def runtime_status(self, principal):
        require(principal, "read")
        with self.store.engine.connect() as connection:
            evidence = [
                r
                for r in self._list(connection, "runtime_health", principal)
                if datetime.fromisoformat(r["expires_at"]) > datetime.now(UTC)
            ]
        return {
            "status": "DEGRADED" if evidence else "UNAVAILABLE",
            "contract_version": "darla-runtime/1",
            "reason": "DEGRADED is runtime-reported, not verified by AXIOM; 120-second heartbeat window; no verified live inference"
            if evidence
            else "No current bound runtime evidence",
            "evidence": evidence,
            "runtime_evidence": policy.RUNTIME_EVIDENCE,
            "runtime_verified_by_axiom": False,
            "heartbeat_window_seconds": policy.HEARTBEAT_WINDOW_SECONDS,
        }
