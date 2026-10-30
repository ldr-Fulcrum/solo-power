"""Validated public contracts; runtime contracts are separately versioned."""

from decimal import Decimal
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Domain(StrEnum):
    BAKERY = "bakery"
    PERSONAL = "personal"


class TaskStatus(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class RunStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    RETRY_PENDING = "RETRY_PENDING"
    UNRESOLVED = "UNRESOLVED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class TaskCreate(Model):
    title: str = Field(min_length=1, max_length=200)
    due_at: str | None = Field(default=None, max_length=40)


class TaskPatch(Model):
    status: TaskStatus


class BakeryPlanCreate(Model):
    title: str = Field(min_length=1, max_length=200)
    minutes_required: int = Field(gt=0, le=100000)
    capacity_minutes: int | None = Field(default=None, gt=0, le=100000)
    provenance: str = Field(default="user_supplied_unverified", max_length=200)


class Ingredient(Model):
    name: str = Field(min_length=1, max_length=100)
    grams: Decimal = Field(gt=0, max_digits=12, decimal_places=3)


class BatchCreate(Model):
    recipe_ref: str = Field(min_length=1, max_length=200)
    base_yield: int = Field(gt=0, le=100000)
    target_yield: int = Field(gt=0, le=100000)
    ingredients: list[Ingredient] = Field(min_length=1, max_length=100)


class OrderDraftCreate(Model):
    description: str = Field(min_length=1, max_length=1000)
    estimated_total_cents: int | None = Field(default=None, ge=0, le=100000000)


class RunCreate(Model):
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any]


class ScheduleCreate(Model):
    title: str = Field(min_length=1, max_length=200)
    interval_seconds: int = Field(ge=60, le=31536000)
    task_title: str = Field(min_length=1, max_length=200)


class ApprovalDecision(Model):
    decision: Literal["APPROVED", "REJECTED"]


class MemoryCreate(Model):
    value: str = Field(min_length=1, max_length=2000)
    remember: Literal[True]


class MemoryPatch(Model):
    value: str = Field(min_length=1, max_length=2000)


class MessageCreate(Model):
    message: str = Field(min_length=1, max_length=4000)
    thread_id: str | None = Field(default=None, max_length=100)


class Empty(Model):
    pass


class Record(BaseModel):
    """Common record envelope; route models refine the resource payload."""

    model_config = ConfigDict(extra="allow")
    id: str
    owner_id: str
    domain: Domain
    synthetic: bool
    created_at: str
    updated_at: str


class TaskRecord(Record):
    title: str
    status: TaskStatus
    due_at: str | None = None


class PlanRecord(Record):
    title: str
    status: Literal["PROPOSED"]
    minutes_required: int
    capacity_minutes: int | None
    capacity_status: Literal["UNKNOWN", "WITHIN_CAPACITY", "OVER_CAPACITY"]
    provenance: str


class BatchRecord(Record):
    recipe_ref: str
    base_yield: int
    target_yield: int
    ingredients: list[dict[str, str]]
    status: Literal["PROPOSED"]
    integration_status: str


class OrderRecord(Record):
    description: str
    estimated_total_cents: int | None
    status: Literal["DRAFT"]
    price_status: str


class RunRecord(Record):
    tool: str
    arguments: dict[str, Any]
    status: RunStatus
    checkpoint: dict[str, Any]
    attempts: int
    max_attempts: int
    next_attempt_at: str
    lease_until: str | None
    error_code: str | None


class ScheduleRecord(Record):
    title: str
    interval_seconds: int
    task_title: str
    next_due_at: str
    worker_operating: bool


class ApprovalRecord(Record):
    purpose: str
    status: Literal["PENDING", "APPROVED", "REJECTED"]
    expires_at: str
    execution_authorized: bool


class MemoryRecord(Record):
    value: str
    provenance: str


class ReceiptRecord(Record):
    actor_id: str
    action: str
    resource_id: str
    outcome: Literal["COMPLETED", "FAILED", "UNRESOLVED"]
    idempotency_key: str
    authority: str
    evidence_hash: str
    error_code: str | None = None


class ErrorDetail(Model):
    code: str
    message: str
    correlation_id: str


class ErrorEnvelope(Model):
    error: ErrorDetail


class Health(Model):
    status: Literal["alive"]


class Readiness(Model):
    application_ready: bool
    database_ready: bool
    autonomy_ready: bool
    worker_operating: bool
    runtime: Literal["UNAVAILABLE", "DEGRADED"]
    runtime_evidence: Literal["RUNTIME_REPORTED_NOT_VERIFIED_BY_AXIOM", "HUMAN_ATTESTED"]
    runtime_verified_by_axiom: Literal[False]
    heartbeat_window_seconds: Literal[120]


class AgentStatus(Model):
    evidence_source: Literal["NONE", "RUNTIME_REPORTED", "HUMAN_ATTESTED_BOOTSTRAP"] = "NONE"
    locked: bool
    status: Literal["UNAVAILABLE", "DEGRADED"]
    contract_version: str
    reason: str
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    runtime_evidence: Literal["RUNTIME_REPORTED_NOT_VERIFIED_BY_AXIOM", "HUMAN_ATTESTED"]
    runtime_verified_by_axiom: Literal[False]
    heartbeat_window_seconds: Literal[120]


class WeeklyGoal(Model):
    target_cents: int
    currency: Literal["USD"]
    actual_cents: int | None
    planned_draft_cents: int
    source_status: str
    target_provenance: str
    gross_sales_note: str
