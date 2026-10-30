"""Explicit healthy-admission precondition for the 92 unchanged v4.1 regressions.

Only historical HTTP proposal requests get short-lived synthetic health fixtures.
The new Request 005 tests are raw and receive no setup. Production has no bypass.
Fixture rows expire immediately after each legacy request, preserving old status tests.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend.app.domain import Domain
from backend.app.security import SCOPES, Principal


@pytest.fixture(autouse=True)
def legacy_healthy_admission(request, monkeypatch):
    if request.node.path.name.startswith("test_request_"):
        return
    original = TestClient.request

    def send(client, method, url, *args, **kwargs):
        headers = kwargs.get("headers") or {}
        if (
            str(method).upper() != "POST"
            or str(url) != "/api/v1/runtime/proposals"
            or headers.get("Authorization") != "Bearer synthetic-agent-bakery"
        ):
            return original(client, method, url, *args, **kwargs)
        services = client.app.state.services
        p = Principal("synthetic-dani", Domain.BAKERY, SCOPES)
        with services.store.transaction() as c:
            health = services._create(
                c,
                "runtime_health",
                p,
                {
                    "session_id": "synthetic-agent-bakery",
                    "expires_at": (datetime.now(UTC) + timedelta(seconds=120)).isoformat(),
                    "fixture": "legacy-v4.1-healthy-admission-only",
                },
            )
        try:
            return original(client, method, url, *args, **kwargs)
        finally:
            with services.store.transaction() as c:
                services._save(
                    c,
                    {
                        **health,
                        "expires_at": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                    },
                )

    monkeypatch.setattr(TestClient, "request", send)
