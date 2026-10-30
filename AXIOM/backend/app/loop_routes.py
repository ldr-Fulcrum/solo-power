"""Explicit v4 routes; OpenShell policy changes belong to Rayzer and LDR."""

from fastapi import Depends, Header, Query

from backend.app.domain import Empty, Record
from backend.app.loop import Claim, HealthEvidence, Refusal, Reply, SupplierText, agent
from backend.app.security import Principal, authenticate


def install(app, services):
    @app.get("/api/v1/runtime/tools", tags=["Runtime"])
    def tools(principal: Principal = Depends(authenticate)):
        return services.runtime_tools(principal)

    @app.get("/api/v1/assistant/messages", tags=["Assistant"])
    def messages(
        principal: Principal = Depends(authenticate),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        return services.list("message", principal, limit, offset)

    @app.get("/api/v1/runtime/assistant/inbox", tags=["Runtime"])
    def inbox(
        principal: Principal = Depends(authenticate),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        agent(principal)
        return services.list("message", principal, limit, offset, "QUEUED")

    @app.post("/api/v1/runtime/assistant/claim", response_model=Record, tags=["Runtime"])
    def claim(
        body: Claim,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        return services.bridge(principal, "claim", key, body.model_dump(mode="json"))

    @app.post("/api/v1/runtime/assistant/replies", response_model=Record, tags=["Runtime"])
    def reply(
        body: Reply,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        return services.bridge(principal, "reply", key, body.model_dump(mode="json"))

    @app.post(
        "/api/v1/assistant/messages/{message_id}/cancel", response_model=Record, tags=["Assistant"]
    )
    def cancel(
        message_id: str,
        body: Empty,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        return services.bridge(principal, "cancel_message", key, {"message_id": message_id})

    @app.post(
        "/api/v1/bakery/supplier-messages", response_model=Record, status_code=201, tags=["Bakery"]
    )
    def supplier(
        body: SupplierText,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        return services.bridge(principal, "supplier", key, body.model_dump(mode="json"))

    @app.get("/api/v1/runtime/untrusted-texts", tags=["Runtime"])
    def texts(
        principal: Principal = Depends(authenticate),
        limit: int = Query(50, ge=1, le=100),
        offset: int = Query(0, ge=0),
    ):
        agent(principal)
        return services.list("supplier", principal, limit, offset)

    @app.post("/api/v1/runtime/refusals", response_model=Record, tags=["Runtime"])
    def refusal(
        body: Refusal,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        return services.bridge(principal, "refusal", key, body.model_dump(mode="json"))

    @app.post("/api/v1/runtime/health", response_model=Record, tags=["Runtime"])
    def health(
        body: HealthEvidence,
        principal: Principal = Depends(authenticate),
        key: str | None = Header(None, alias="Idempotency-Key"),
    ):
        return services.bridge(principal, "health", key, body.model_dump(mode="json"))
