"""Server-bound synthetic sessions, never caller-asserted permissions."""

from dataclasses import dataclass

from fastapi import Request, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.domain import Domain

bearer = HTTPBearer(auto_error=False)


class Fault(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status, self.code, self.message = status, code, message
        super().__init__(message)


@dataclass(frozen=True)
class Principal:
    id: str
    domain: Domain
    scopes: frozenset[str]
    actor_kind: str = "human"
    synthetic: bool = True
    actor_id: str | None = None
    session_id: str | None = None


SCOPES = frozenset(
    {
        "read",
        "task.write",
        "plan.write",
        "draft.write",
        "run.write",
        "schedule.write",
        "approval.decide",
        "memory.write",
    }
)


def authenticate(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Security(bearer)
) -> Principal:
    settings = request.app.state.services.settings
    if not settings.synthetic_mode or credentials is None:
        raise Fault(401, "AUTH_REQUIRED", "An authenticated session is required")
    sessions = {
        "synthetic-guard-bakery": Principal(
            "synthetic-dani",
            Domain.BAKERY,
            frozenset({"guard"}),
            actor_kind="guard",
            actor_id="synthetic-runtime-guard",
            session_id="synthetic-guard-session",
        ),
        "synthetic-dani-bakery": Principal("synthetic-dani", Domain.BAKERY, SCOPES),
        "synthetic-dani-personal": Principal(
            "synthetic-dani", Domain.PERSONAL, SCOPES - {"plan.write", "draft.write"}
        ),
        "synthetic-reader-bakery": Principal(
            "synthetic-reader", Domain.BAKERY, frozenset({"read"})
        ),
        "synthetic-agent-bakery": Principal(
            "synthetic-dani",
            Domain.BAKERY,
            frozenset(
                {"read", "run.write", "task.write", "plan.write", "draft.write", "runtime.propose"}
            ),
            actor_kind="agent",
            actor_id="synthetic-darla-agent",
            session_id="synthetic-agent-bakery",
        ),
    }
    principal = sessions.get(credentials.credentials)
    if principal is None:
        raise Fault(401, "AUTH_REQUIRED", "Invalid synthetic session")
    from backend.app.gate_state import request_context

    context = request_context.get()
    if context is not None:
        context["principal"] = principal
    if principal.actor_kind == "guard" and (request.method, request.url.path) not in {
        ("GET", "/api/v1/agent/status"),
        ("POST", "/api/v1/runtime/lock"),
        ("GET", "/api/v1/runtime/events"),
    }:
        raise Fault(
            403, "GUARD_SCOPE_DENIED", "Guard is restricted to runtime status, lock and event feed"
        )
    return principal


def require(principal: Principal, scope: str, bakery: bool = False):
    if scope not in principal.scopes or (bakery and principal.domain != Domain.BAKERY):
        raise Fault(403, "SCOPE_DENIED", "Session lacks the required permission")
