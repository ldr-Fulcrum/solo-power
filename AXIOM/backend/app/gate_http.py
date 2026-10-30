"""Bounded Request 005 outcomes; effects and minimal refusal facts commit together."""

import asyncio
import json
from weakref import WeakKeyDictionary

from backend.app.gate import ADMISSIONS
from backend.app.gate_state import connection_context, request_context

CONTROL_PATHS = {
    "/api/v1/runtime/bootstrap",
    "/api/v1/agent/status",
    "/api/v1/runtime/lock",
    "/api/v1/runtime/rearm",
    "/api/v1/runtime/journal-anchors",
    "/api/v1/runtime/events",
    "/api/v1/runtime/health",
}


def error(code, status):
    data = json.dumps(
        {
            "error": {
                "code": code,
                "message": "Control request could not be completed",
                "correlation_id": "runtime-control",
            }
        }
    ).encode()
    return [
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")],
        },
        {"type": "http.response.body", "body": data},
    ]


class GateHTTP:
    def __init__(self, app, services):
        self.app = app
        self.services = services
        self.locks = WeakKeyDictionary()

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        method = scope.get("method", "")
        if scope["type"] != "http" or not (
            path in CONTROL_PATHS or (path in ADMISSIONS and method == "POST")
        ):
            return await self.app(scope, receive, send)
        raw = b""
        while True:
            msg = await receive()
            if msg["type"] == "http.disconnect":
                return
            raw += msg.get("body", b"")
            if len(raw) > 65536:
                for part in error("REQUEST_TOO_LARGE", 413):
                    await send(part)
                return
            if not msg.get("more_body", False):
                break
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            body = {}
        tool = None
        if isinstance(body, dict):
            result = body.get("result", {})
            action = result.get("action", {}) if isinstance(result, dict) else {}
            proposed = action.get("requested_tool") if isinstance(action, dict) else None
            if isinstance(proposed, str) and proposed in {
                "task.create",
                "plan.create",
                "batch.create",
                "draft.create",
                *self.services.tool_registry().keys(),
            }:
                tool = proposed
        context = {"principal": None, "tool": tool}
        messages = []
        async with self.locks.setdefault(asyncio.get_running_loop(), asyncio.Lock()):
            c = None
            ct = None
            rt = None
            try:

                def begin():
                    value = self.services.store.engine.connect()
                    try:
                        value.exec_driver_sql("BEGIN IMMEDIATE")
                    except BaseException:
                        value.close()
                        raise
                    return value

                c = await asyncio.to_thread(begin)
                ct = connection_context.set(c)
                rt = request_context.set(context)
                delivered = False

                async def replay():
                    nonlocal delivered
                    if not delivered:
                        delivered = True
                        return {"type": "http.request", "body": raw, "more_body": False}
                    return await receive()

                async def capture(message):
                    messages.append(message)
                    if sum(len(m.get("body", b"")) for m in messages) > 2097152:
                        raise RuntimeError("Response too large")

                await self.app(scope, replay, capture)
                status = next(m["status"] for m in messages if m["type"] == "http.response.start")
                p = context["principal"]
                if status >= 400 and p is not None:
                    try:
                        value = json.loads(b"".join(m.get("body", b"") for m in messages))
                    except (ValueError, UnicodeError):
                        value = {}
                    code = (
                        value.get("error", {}).get("code", "HTTP_REFUSED")
                        if isinstance(value, dict)
                        else "HTTP_REFUSED"
                    )
                    if p.actor_kind == "agent" and method == "POST" and path in ADMISSIONS:
                        self.services.fresh_health(c, p)
                        self.services.append_fact(c, p, "proposal_refused", str(code), tool=tool)
                    elif path == "/api/v1/runtime/journal-anchors" and method == "POST":
                        self.services.append_fact(c, p, "anchor_refused", str(code))
                await asyncio.to_thread(c.commit)
            except Exception:
                if c is not None:
                    await asyncio.to_thread(c.rollback)
                messages = error("CONTROL_AUDIT_UNAVAILABLE", 503)
            finally:
                if rt is not None:
                    request_context.reset(rt)
                if ct is not None:
                    connection_context.reset(ct)
                if c is not None:
                    await asyncio.to_thread(c.close)
            for message in messages:
                await send(message)
