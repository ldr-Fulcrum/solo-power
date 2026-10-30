"""Rayzer-owned runtimes implement these contracts; none is installed here."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import Field

from backend.app.domain import Domain, Model, TaskCreate
from backend.app.security import Fault, Principal, require

CONTRACT_VERSION = "darla-runtime/1"


class Budget(Model):
    max_tokens: int = Field(gt=0, le=100000)
    max_tool_calls: int = Field(ge=0, le=20)
    max_cost_usd: Decimal = Field(ge=0, le=100)


class AuthorizationEnvelope(Model):
    principal_id: str
    session_id: str
    domain: Domain
    permitted_scopes: list[str]
    approval_reference: str | None = None
    expires_at: datetime


class RuntimeInput(Model):
    contract_version: Literal["darla-runtime/1"] = CONTRACT_VERSION
    principal_id: str
    agent_id: str
    request_id: str = Field(min_length=1, max_length=90)
    goal: str = Field(min_length=1, max_length=2000)
    domain: Domain
    memory_references: list[str] = Field(default_factory=list, max_length=100)
    tool_allowlist: list[str] = Field(max_length=20)
    authorization: AuthorizationEnvelope
    budget: Budget
    deadline: datetime
    task_state: dict[str, Any] = Field(default_factory=dict)
    data_classification: Literal["synthetic", "private"] = "synthetic"


class ProposedAction(Model):
    requested_tool: str
    parameters: dict[str, Any]
    summary: str = Field(min_length=1, max_length=300)
    status: Literal["PROPOSED"] = "PROPOSED"
    requires_approval: bool = False
    provenance_references: list[str] = Field(default_factory=list)


class RuntimeResult(Model):
    contract_version: Literal["darla-runtime/1"] = CONTRACT_VERSION
    request_id: str = Field(min_length=1, max_length=90)
    actor_id: str
    action: ProposedAction | None = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    error_classification: str | None = None
    test_double: bool = False


class ProviderRequest(Model):
    request_id: str = Field(min_length=1, max_length=90)
    configured_model: str
    endpoint_identity: str
    timeout_seconds: int = Field(ge=1, le=120)
    max_tokens: int = Field(gt=0, le=100000)
    context_reference: str


class ProviderResult(Model):
    provider: str
    configured_model: str
    resolved_model: str
    endpoint_identity: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: Decimal = Field(ge=0)
    tool_call_capable: bool
    structured_output_valid: bool
    classification: Literal["SUCCESS", "TIMEOUT", "UNAVAILABLE", "INVALID_OUTPUT", "DENIED"]
    redacted_receipt_reference: str


class RuntimeHealth(Model):
    status: Literal["AVAILABLE", "UNAVAILABLE", "DEGRADED", "TEST_DOUBLE"]
    contract_version: Literal["darla-runtime/1"] = CONTRACT_VERSION
    evidence_reference: str | None = None


class RuntimeEvent(Model):
    run_id: str
    request_id: str = Field(min_length=1, max_length=90)
    event_id: str = Field(min_length=1, max_length=90)
    kind: Literal["CHECKPOINT", "ERROR"]
    checkpoint_reference: str | None = Field(default=None, max_length=200)
    error_classification: str | None = None


class RuntimeProposal(Model):
    input: RuntimeInput
    result: RuntimeResult


class AgentRuntime(Protocol):
    def propose(self, request: RuntimeInput) -> RuntimeResult: ...
    def health(self) -> RuntimeHealth: ...


class InferenceProvider(Protocol):
    def infer(self, request: ProviderRequest) -> ProviderResult: ...


class AgentMemoryContext(Protocol):
    def permitted_references(self, principal: Principal) -> list[str]: ...


class ExecutionPolicy(Protocol):
    def authorize(
        self, principal: Principal, request: RuntimeInput, action: ProposedAction
    ) -> None: ...


class RuntimeEventSink(Protocol):
    def emit(self, principal: Principal, event: RuntimeEvent) -> None: ...


class ReceiptEmitter(Protocol):
    def flush_journal(self) -> None: ...


class ToolGateway(Protocol):
    def queue(
        self, principal: Principal, request: RuntimeInput, result: RuntimeResult, key: str
    ) -> dict: ...


class ApplicationGateway:
    """The server session principal is trusted; model-provided identities are not."""

    def __init__(self, services):
        self.services = services

    def authorize(self, principal, request, action):
        require(principal, "run.write")
        timestamp = datetime.now(UTC)
        auth = request.authorization
        if principal.actor_kind == "agent" and (
            request.agent_id != principal.actor_id or auth.session_id != principal.session_id
        ):
            raise Fault(
                403,
                "RUNTIME_SESSION_MISMATCH",
                "Runtime actor/session does not match the server-authenticated agent",
            )
        if request.deadline.tzinfo is None or auth.expires_at.tzinfo is None:
            raise Fault(
                422, "TIMEZONE_REQUIRED", "Runtime deadlines require timezone-aware timestamps"
            )
        if request.deadline <= timestamp or auth.expires_at <= timestamp:
            raise Fault(403, "EXPIRED_AUTHORITY", "Runtime authority or deadline expired")
        if (
            request.principal_id != principal.id
            or auth.principal_id != principal.id
            or request.domain != principal.domain
            or auth.domain != principal.domain
        ):
            raise Fault(
                403,
                "IDENTITY_MISMATCH",
                "Runtime request does not match the authenticated server session",
            )
        if not set(auth.permitted_scopes).issubset(principal.scopes):
            raise Fault(403, "SCOPE_ESCALATION", "Runtime envelope cannot grant permissions")
        if action.requested_tool not in request.tool_allowlist or request.budget.max_tool_calls < 1:
            raise Fault(403, "TOOL_DENIED", "Tool is outside the bounded allowlist or budget")
        if auth.approval_reference is not None:
            raise Fault(
                403,
                "APPROVAL_NOT_BOUND",
                "Model-supplied approval references cannot grant execution",
            )
        if request.data_classification != "synthetic" or not principal.synthetic:
            raise Fault(
                403,
                "PRIVATE_RUNTIME_DISABLED",
                "Runtime contracts currently admit synthetic fixtures only",
            )
        for reference in request.memory_references:
            self.services.get("memory", reference, principal)
        self.services.validate_tool(
            replace(principal, scopes=frozenset(auth.permitted_scopes)),
            action.requested_tool,
            action.parameters,
        )

    def queue(self, principal, request, result, key):
        if (
            result.request_id != request.request_id
            or result.actor_id != request.agent_id
            or result.action is None
        ):
            raise Fault(
                422, "RUNTIME_RESULT_MISMATCH", "Runtime result does not match the bounded request"
            )
        self.authorize(principal, request, result.action)
        return self.services.mutate(
            replace(principal, scopes=frozenset(request.authorization.permitted_scopes)),
            "run.create",
            "runtime:" + request.request_id,
            {
                "tool": result.action.requested_tool,
                "arguments": result.action.parameters,
                "requires_approval": result.action.requires_approval,
                "summary": result.action.summary,
                "runtime_context": {
                    "provenance_references": result.action.provenance_references,
                    "test_double": result.test_double,
                    "request_id": request.request_id,
                    "agent_id": request.agent_id,
                    "session_id": request.authorization.session_id,
                    "expires_at": min(
                        request.deadline, request.authorization.expires_at
                    ).isoformat(),
                },
            },
        )


class FakeRuntime:
    """Executable conformance double. Never used as the assistant's live runtime."""

    def health(self):
        return RuntimeHealth(status="TEST_DOUBLE")

    def propose(self, request):
        task = TaskCreate(title="Synthetic runtime conformance task")
        return RuntimeResult(
            request_id=request.request_id,
            actor_id=request.agent_id,
            action=ProposedAction(
                requested_tool="task.create",
                parameters=task.model_dump(mode="json"),
                summary="Propose one synthetic internal task",
            ),
            test_double=True,
        )
