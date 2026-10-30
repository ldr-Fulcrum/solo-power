"""Versioned application API. Use the explicit opt-in server entrypoint for binding."""

from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from backend.app import approval_policy as policy
from backend.app.agent_runtime import ApplicationGateway, RuntimeEvent, RuntimeProposal
from backend.app.bakeoff import Services
from backend.app.config import Settings
from backend.app.domain import (
    AgentStatus,
    ApprovalDecision,
    ApprovalRecord,
    BakeryPlanCreate,
    BatchCreate,
    BatchRecord,
    Empty,
    ErrorEnvelope,
    Health,
    MemoryCreate,
    MemoryPatch,
    MemoryRecord,
    MessageCreate,
    OrderDraftCreate,
    OrderRecord,
    PlanRecord,
    Readiness,
    ReceiptRecord,
    Record,
    RunCreate,
    RunRecord,
    ScheduleCreate,
    ScheduleRecord,
    TaskCreate,
    TaskPatch,
    TaskRecord,
    WeeklyGoal,
)
from backend.app.security import Fault, Principal, authenticate, require


class Page[T](BaseModel):
    items: list[T]

    total: int

    limit: int

    offset: int


class MemoryChange(BaseModel):
    id: str

    saved: bool = False

    corrected: bool = False

    deleted: bool = False

    provenance: str | None = None


ERRORS = {
    status: {"model": ErrorEnvelope}
    for status in (400, 401, 403, 404, 409, 422, 423, 429, 500, 503)
}


def create_app(settings: Settings | None = None):

    services = Services(settings or Settings.from_environment())

    @asynccontextmanager
    async def lifespan(app):

        yield

        if services.bakeoff:
            services.bakeoff.close()
        services.store.engine.dispose()

    app = FastAPI(
        title="Darla Giggles Application API",
        version="0.1.0",
        lifespan=lifespan,
        responses=ERRORS,
        description="Local-first application API. Synthetic sessions require explicit opt-in. The optional task-hub adapter requires explicit configuration.",
    )

    app.state.services = services

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=services.settings.allowed_hosts)

    @app.middleware("http")
    async def correlation(request: Request, call_next):

        request.state.correlation_id = str(uuid4())

        response = await call_next(request)

        response.headers["X-Correlation-ID"] = request.state.correlation_id

        response.headers["Cache-Control"] = "no-store"

        return response

    def error_response(request, status, code, message):

        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "correlation_id": getattr(request.state, "correlation_id", "unavailable"),
                }
            },
        )

    @app.exception_handler(Fault)
    async def fault(request: Request, error: Fault):

        return error_response(request, error.status, error.code, error.message)

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, error: RequestValidationError):

        return error_response(
            request, 422, "VALIDATION_ERROR", "Request does not match the published schema"
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):

        return error_response(
            request, error.status_code, "HTTP_ERROR", "HTTP request could not be served"
        )

    @app.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception):

        return error_response(
            request, 500, "INTERNAL_ERROR", "Application operation failed; no completion is implied"
        )

    def mutate(principal, operation, key, payload, record_id=None):

        if principal.actor_kind == "agent":
            raise Fault(
                403,
                "RUNTIME_GATEWAY_REQUIRED",
                "Agents must use the validated runtime proposal gateway",
            )

        return services.mutate(
            principal, operation, key, payload.model_dump(mode="json"), record_id
        )

    def listing(kind, principal, limit, offset, status=None):

        return services.list(kind, principal, limit, offset, status)

    @app.get("/health/live", response_model=Health, tags=["System"])
    def live():

        return {"status": "alive"}

    @app.get("/health/ready", response_model=Readiness, tags=["System"])
    def ready(principal: Principal = Depends(authenticate)):

        require(principal, "read")

        try:
            database_ready = services.store.ready()

        except Exception:
            raise Fault(
                503, "DATABASE_UNAVAILABLE", "Required application database is unavailable"
            ) from None

        return {
            "application_ready": database_ready,
            "database_ready": database_ready,
            "autonomy_ready": False,
            "worker_operating": False,
            "runtime": services.runtime_status(principal)["status"],
            "runtime_evidence": services.runtime_status(principal)["runtime_evidence"],
            "runtime_verified_by_axiom": False,
            "heartbeat_window_seconds": policy.HEARTBEAT_WINDOW_SECONDS,
        }

    @app.get("/api/v1/system/capabilities", response_model=dict[str, str], tags=["System"])
    def capabilities(principal: Principal = Depends(authenticate)):

        require(principal, "read")

        return {
            "application_state": "DURABLE_SQLITE",
            "authentication": "SYNTHETIC_NONPRODUCTION"
            if services.settings.synthetic_mode
            else "NOT_CONFIGURED_DENY_ALL",
            "agent": services.runtime_status(principal)["status"],
            "agent_evidence": services.runtime_status(principal)["runtime_evidence"],
            "agent_heartbeat_window_seconds": str(policy.HEARTBEAT_WINDOW_SECONDS),
            "external_effects": (
                "BAKEOFF_APPROVAL_GOVERNED"
                if services.bakeoff.role == "STAFF"
                else "BAKEOFF_READS_ONLY"
            )
            if services.bakeoff_active()
            else "DISABLED",
            "worker": "FINITE_EXPLICIT_TICKS_ONLY",
            "receipts": "LOCAL_DURABLE_NOT_FORTRESS",
            "memory": "SCOPED_USER_CONTROLLED",
            "tool.task.create": "LOCAL_INTERNAL",
            "tool.plan.create": "LOCAL_INTERNAL",
            "tool.batch.create": "LOCAL_CALCULATION_NOT_SCALED_BAKING",
            "tool.draft.create": "LOCAL_NONBINDING_HUMAN_APPROVAL",
            "inventory": "MISSING_SOURCE",
        }

    @app.get("/api/v1/integrations", response_model=dict[str, str], tags=["System"])
    def integrations(principal: Principal = Depends(authenticate)):

        require(principal, "read")

        return {
            "contract_version": "darla-runtime/1",
            "darla_runtime": "NOT_CONNECTED",
            "hermes": "UNAVAILABLE",
            "nemoclaw": "UNAVAILABLE",
            "openshell": "UNAVAILABLE",
            "nebius": "NOT_CONNECTED",
            "lightning": "PRIOR_TEST_CONFIRMED",
            "super": "NOT_YET_VERIFIED",
            "crumbcraft": "MISSING_SOURCE",
            "scaled_baking": "MISSING_SOURCE",
            "bakeoff": services.bakeoff_status(),
            "look_no_hands": "MISSING_SOURCE",
            "annual_playbook": "PARTIAL_REFERENCE_ONLY",
            "infinite_network_world": "NOT_CONNECTED",
            "fortress_receipt_bridge": "NOT_CONNECTED",
        }

    @app.get("/api/v1/agent/status", response_model=AgentStatus, tags=["Agent"])
    def agent_status(principal: Principal = Depends(authenticate)):

        require(principal, "guard" if principal.actor_kind == "guard" else "read")

        return services.runtime_status(principal)

    @app.post("/api/v1/assistant/messages", response_model=Record, tags=["Assistant"])
    def messages(
        body: MessageCreate,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return services.bridge(principal, "message", key, body.model_dump(mode="json"))

    @app.get("/api/v1/assistant/threads/{thread_id}", response_model=Record, tags=["Assistant"])
    def threads(thread_id: str, principal: Principal = Depends(authenticate)):

        return services.get("thread", thread_id, principal)

    @app.get("/api/v1/bakery/goals/weekly", response_model=WeeklyGoal, tags=["Bakery"])
    def weekly(principal: Principal = Depends(authenticate)):

        return services.weekly_goal(principal)

    @app.post("/api/v1/bakery/plans", response_model=PlanRecord, status_code=201, tags=["Bakery"])
    def plan_create(
        body: BakeryPlanCreate,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return mutate(principal, "plan.create", key, body)

    @app.get("/api/v1/bakery/plans/{plan_id}", response_model=PlanRecord, tags=["Bakery"])
    def plan_get(plan_id: str, principal: Principal = Depends(authenticate)):

        require(principal, "read", bakery=True)

        return services.get("plan", plan_id, principal)

    @app.get("/api/v1/bakery/products", response_model=Page[Record], tags=["Bakery"])
    def products(
        principal: Principal = Depends(authenticate),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):

        require(principal, "read", bakery=True)

        return listing("product", principal, limit, offset)

    @app.post(
        "/api/v1/bakery/batch-plans", response_model=BatchRecord, status_code=201, tags=["Bakery"]
    )
    def batch_create(
        body: BatchCreate,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return mutate(principal, "batch.create", key, body)

    @app.get("/api/v1/orders", response_model=Page[OrderRecord], tags=["Orders"])
    def orders(
        principal: Principal = Depends(authenticate),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):

        require(principal, "read", bakery=True)

        return listing("order", principal, limit, offset)

    @app.post("/api/v1/orders/drafts", response_model=OrderRecord, status_code=201, tags=["Orders"])
    def draft_create(
        body: OrderDraftCreate,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return mutate(principal, "draft.create", key, body)

    @app.get("/api/v1/tasks", response_model=Page[TaskRecord], tags=["Tasks"])
    def tasks(
        principal: Principal = Depends(authenticate),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        status: str | None = Query(None, max_length=30),
    ):

        return listing("task", principal, limit, offset, status)

    @app.post("/api/v1/tasks", response_model=TaskRecord, status_code=201, tags=["Tasks"])
    def task_create(
        body: TaskCreate,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return mutate(principal, "task.create", key, body)

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskRecord, tags=["Tasks"])
    def task_get(task_id: str, principal: Principal = Depends(authenticate)):

        return services.get("task", task_id, principal)

    @app.patch("/api/v1/tasks/{task_id}", response_model=TaskRecord, tags=["Tasks"])
    def task_patch(
        task_id: str,
        body: TaskPatch,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return mutate(principal, "task.patch", key, body, task_id)

    @app.post("/api/v1/runs", response_model=RunRecord, status_code=201, tags=["Runs"])
    def run_create(
        body: RunCreate,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return mutate(principal, "run.create", key, body)

    @app.get("/api/v1/runs", response_model=Page[RunRecord], tags=["Runs"])
    def runs(
        principal: Principal = Depends(authenticate),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
        status: str | None = Query(None, max_length=30),
    ):

        return listing("run", principal, limit, offset, status)

    @app.get("/api/v1/runs/{run_id}", response_model=RunRecord, tags=["Runs"])
    def run_get(run_id: str, principal: Principal = Depends(authenticate)):

        return services.get("run", run_id, principal)

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=RunRecord, tags=["Runs"])
    def run_cancel(
        run_id: str,
        body: Empty,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return mutate(principal, "run.cancel", key, body, run_id)

    @app.get("/api/v1/schedules", response_model=Page[ScheduleRecord], tags=["Schedules"])
    def schedules(
        principal: Principal = Depends(authenticate),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):

        return listing("schedule", principal, limit, offset)

    @app.post(
        "/api/v1/schedules", response_model=ScheduleRecord, status_code=201, tags=["Schedules"]
    )
    def schedule_create(
        body: ScheduleCreate,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return mutate(principal, "schedule.create", key, body)

    @app.get("/api/v1/approvals", response_model=Page[ApprovalRecord], tags=["Approvals"])
    def approvals(
        principal: Principal = Depends(authenticate),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):

        return listing("approval", principal, limit, offset)

    @app.get("/api/v1/approvals/{approval_id}", response_model=ApprovalRecord, tags=["Approvals"])
    def approval_get(approval_id: str, principal: Principal = Depends(authenticate)):

        return services.get("approval", approval_id, principal)

    @app.post(
        "/api/v1/approvals/{approval_id}/decision",
        response_model=ApprovalRecord,
        tags=["Approvals"],
    )
    def approval_decide(
        approval_id: str,
        body: ApprovalDecision,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return mutate(principal, "approval.decision", key, body, approval_id)

    @app.get("/api/v1/receipts", response_model=Page[ReceiptRecord], tags=["Receipts"])
    def receipts(
        principal: Principal = Depends(authenticate),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):

        return listing("receipt", principal, limit, offset)

    @app.get("/api/v1/receipts/{receipt_id}", response_model=ReceiptRecord, tags=["Receipts"])
    def receipt_get(receipt_id: str, principal: Principal = Depends(authenticate)):

        return services.get("receipt", receipt_id, principal)

    @app.get("/api/v1/memory", response_model=Page[MemoryRecord], tags=["Memory"])
    def memories(
        principal: Principal = Depends(authenticate),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):

        return listing("memory", principal, limit, offset)

    @app.post("/api/v1/memory", response_model=MemoryChange, status_code=201, tags=["Memory"])
    def memory_create(
        body: MemoryCreate,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return mutate(principal, "memory.create", key, body)

    @app.patch("/api/v1/memory/{memory_id}", response_model=MemoryChange, tags=["Memory"])
    def memory_patch(
        memory_id: str,
        body: MemoryPatch,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return mutate(principal, "memory.patch", key, body, memory_id)

    @app.delete("/api/v1/memory/{memory_id}", response_model=MemoryChange, tags=["Memory"])
    def memory_delete(
        memory_id: str,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return mutate(principal, "memory.delete", key, Empty(), memory_id)

    @app.post(
        "/api/v1/runtime/proposals", response_model=RunRecord, status_code=201, tags=["Runtime"]
    )
    def runtime_proposal(
        body: RuntimeProposal,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        require(principal, "runtime.propose")

        if principal.actor_kind != "agent":
            raise Fault(
                403,
                "AGENT_SESSION_REQUIRED",
                "Runtime proposals require a server-bound agent session",
            )

        if not key:
            raise Fault(400, "IDEMPOTENCY_REQUIRED", "Runtime proposals require Idempotency-Key")

        try:
            return ApplicationGateway(services).queue(principal, body.input, body.result, key)

        except Fault as error:
            # Authenticated proposal denial is server evidence, not a model-authored receipt.

            with services.store.transaction() as connection:
                denied = services._create(
                    connection,
                    "denial",
                    principal,
                    {
                        "request_id": body.input.request_id,
                        "requested_tool": body.result.action.requested_tool
                        if body.result.action
                        else None,
                        "error_code": error.code,
                    },
                )

                services._receipt(
                    connection,
                    principal,
                    "runtime.proposal.denied",
                    denied,
                    "denial:" + denied["id"],
                    outcome="FAILED",
                    error=error.code,
                )

            raise

    @app.post("/api/v1/runtime/events", response_model=RunRecord, tags=["Runtime"])
    def runtime_event(
        body: RuntimeEvent,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):

        return services.runtime_event(principal, body, key)

    from backend.app.loop_routes import install

    install(app, services)

    from backend.app.gate_http import GateHTTP
    from backend.app.gate_routes import install as install_gate

    install_gate(app, services)
    app.add_middleware(GateHTTP, services=services)

    # Serve the built frontend on the same loopback origin, after all API routes.

    from fastapi.staticfiles import StaticFiles

    frontend = services.settings.root / "frontend/dist"

    if frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")

    return app
