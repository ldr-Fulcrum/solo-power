"""The three Request 005 HTTP surfaces; no activation or extra runtime authority."""

from datetime import UTC, datetime

from fastapi import Depends, Header, Query
from pydantic import Field, field_validator

from backend.app.domain import Model
from backend.app.security import Fault, Principal, authenticate


class LockBody(Model):
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("A reason is required")
        return value.strip()


class RearmBody(Model):
    note: str = Field(min_length=1, max_length=500)

    @field_validator("note")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("A human note is required")
        return value.strip()


class BootstrapBody(RearmBody):
    minutes: int = Field(default=10, ge=1, le=15, strict=True)


class AnchorBody(Model):
    journal_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_.:-]+$")
    seq: int = Field(ge=1, strict=True)
    head_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def install(app, services):
    @app.post("/api/v1/runtime/bootstrap", tags=["Runtime control"])
    def bootstrap(
        body: BootstrapBody,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        return services.bootstrap(principal, body.model_dump(), key)

    @app.post("/api/v1/runtime/lock", tags=["Runtime control"])
    def lock(
        body: LockBody,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        return services.control_change(principal, "lock", body.reason, key)

    @app.post("/api/v1/runtime/rearm", tags=["Runtime control"])
    def rearm(
        body: RearmBody,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        return services.control_change(principal, "rearm", body.note, key)

    @app.post("/api/v1/runtime/journal-anchors", status_code=201, tags=["Runtime control"])
    def anchor(body: AnchorBody, principal: Principal = Depends(authenticate)):
        return services.anchor_append(principal, body.model_dump())

    @app.get("/api/v1/runtime/journal-anchors", tags=["Runtime control"])
    def anchors(
        principal: Principal = Depends(authenticate),
        journal_id: str | None = Query(None, max_length=100),
        since: datetime | None = None,
        after: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ):
        if since is not None and since.tzinfo is None:
            raise Fault(422, "TIMEZONE_REQUIRED", "since requires a timezone offset")
        timestamp = since.astimezone(UTC).isoformat(timespec="milliseconds") if since else None
        return services.anchor_list(principal, journal_id, timestamp, after, limit)

    @app.get("/api/v1/runtime/events", tags=["Runtime control"])
    def events(
        principal: Principal = Depends(authenticate),
        since: int = Query(0, ge=0),
        limit: int = Query(100, ge=1, le=500),
    ):
        return services.event_feed(principal, since, limit)
