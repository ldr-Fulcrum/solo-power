"""Small host-only client for the pinned, unchanged optional task-hub adapter MCP server."""

import copy
import hashlib
import itertools
import json
import os
import re
from pathlib import Path

import httpx
from jsonschema import Draft202012Validator, FormatChecker

PIN = Path(__file__).resolve().parents[2] / "contracts/task_hub/tool_arguments_v1.json"
PIN_SHA256 = "b9c1a1221dbb13a944cbc2dab9d50ac11c431315a8236dd456d718a9a55441e2"
READS = {"whoami", "get_dashboard", "list_tasks", "get_task", "list_incidents", "list_equipment"}
CHANGES = {"update_task_status", "claim_task", "create_incident", "log_maintenance"}
ALLOWED = READS | CHANGES


def argument_schema(value):
    """Keep validation constraints; ignore remote prose and annotations."""
    if isinstance(value, list):
        return [argument_schema(v) for v in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, child in value.items():
        if key in {"title", "description", "$comment", "examples", "default"}:
            continue
        if key in {"properties", "$defs", "definitions"}:
            result[key] = {name: argument_schema(schema) for name, schema in child.items()}
        else:
            result[key] = argument_schema(child)
    return result


def pinned_tools():
    raw = PIN.read_bytes()
    if hashlib.sha256(raw).hexdigest() != PIN_SHA256:
        raise BakeoffFailure("BAKEOFF_SCHEMA_PIN_INVALID")
    return json.loads(raw)["tools"]


class BakeoffFailure(Exception):
    def __init__(self, code, http_status=None, unknown=False):
        self.code = code
        self.http_status = http_status
        self.unknown = unknown
        super().__init__(code)


def failure_status(status, change=False):
    code = {
        401: "BAKEOFF_UNAUTHORIZED",
        403: "BAKEOFF_FORBIDDEN",
        404: "BAKEOFF_NOT_FOUND",
        409: "BAKEOFF_CONFLICT",
        422: "BAKEOFF_VALIDATION",
        429: "BAKEOFF_RATE_LIMIT",
    }.get(status, "BAKEOFF_HTTP_ERROR")
    return BakeoffFailure(code, status, change and status >= 500)


class Adapter:
    def __init__(self, url, token, transport=None, timeout=5.0):
        parsed = httpx.URL(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.host
            or parsed.userinfo
            or parsed.query
            or parsed.fragment
            or parsed.path != "/mcp"
        ):
            raise BakeoffFailure("BAKEOFF_CONFIG")
        if not isinstance(token, str) or not token or any(ord(c) < 33 for c in token):
            raise BakeoffFailure("BAKEOFF_TOKEN_UNAVAILABLE")
        self.url = str(parsed)
        self._token = token
        self._ids = itertools.count(1)
        self.role = None
        self.ready = False
        self.warning = "BAKEOFF_NOT_CHECKED"
        self.client = httpx.Client(
            transport=transport,
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-11-25",
            },
        )

    @classmethod
    def from_environment(cls):
        url = os.environ.get("DARLA_BAKEOFF_URL")
        if not url:
            return None
        name = os.environ.get("DARLA_BAKEOFF_TOKEN_ENV")
        if not name:
            raise BakeoffFailure("BAKEOFF_TOKEN_UNAVAILABLE")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", name):
            raise BakeoffFailure("BAKEOFF_CONFIG")
        return cls(url, os.environ.get(name, ""))

    def clean(self, value):
        if isinstance(value, str):
            return value.replace(self._token, "[REDACTED]")
        if isinstance(value, list):
            return [self.clean(v) for v in value]
        if isinstance(value, dict):
            return {self.clean(k): self.clean(v) for k, v in value.items()}
        return value

    def _rpc(self, method, params=None, change=False, notification=False):
        identifier = next(self._ids)
        payload = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            payload["id"] = identifier
        try:
            response = self.client.post(self.url, json=payload)
        except httpx.ConnectError:
            raise BakeoffFailure("BAKEOFF_UNREACHABLE") from None
        except httpx.ConnectTimeout:
            raise BakeoffFailure("BAKEOFF_TIMEOUT") from None
        except httpx.TimeoutException:
            raise BakeoffFailure("BAKEOFF_TIMEOUT", unknown=change) from None
        except httpx.TransportError:
            raise BakeoffFailure("BAKEOFF_TRANSPORT_ERROR", unknown=change) from None
        except Exception:
            raise BakeoffFailure("BAKEOFF_TRANSPORT_ERROR", unknown=change) from None
        if response.status_code >= 400:
            raise failure_status(response.status_code, change)
        if notification:
            if response.status_code not in {200, 202, 204}:
                raise BakeoffFailure("BAKEOFF_PROTOCOL_ERROR")
            return None
        if len(response.content) > 2097152:
            raise BakeoffFailure("BAKEOFF_RESPONSE_TOO_LARGE", unknown=change)
        try:
            value = self.clean(response.json())
        except Exception:
            raise BakeoffFailure("BAKEOFF_PROTOCOL_ERROR", unknown=change) from None
        if (
            not isinstance(value, dict)
            or value.get("jsonrpc") != "2.0"
            or value.get("id") != identifier
        ):
            raise BakeoffFailure("BAKEOFF_PROTOCOL_ERROR", unknown=change)
        if "error" in value:
            code = value["error"].get("code") if isinstance(value["error"], dict) else None
            raise BakeoffFailure(
                "BAKEOFF_PROTOCOL_ERROR",
                422 if code == -32602 else 404 if code == -32601 else None,
                change and code not in {-32602, -32601, -32600},
            )
        result = value.get("result")
        if not isinstance(result, dict):
            raise BakeoffFailure("BAKEOFF_PROTOCOL_ERROR", unknown=change)
        return result

    def check_role(self):
        result = self._rpc("tools/call", {"name": "whoami", "arguments": {}})
        data = self._structured(result, False)
        role = data.get("role")
        if role not in {"STAFF", "OWNER", "MANAGER"}:
            raise BakeoffFailure("BAKEOFF_ROLE_INVALID")
        self.role = role
        self.warning = "" if role == "STAFF" else "STAFF_TOKEN_REQUIRED_CHANGES_DISABLED"
        return role

    def start(self):
        self.ready = False
        value = self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "axiom-bakeoff-adapter", "version": "1"},
            },
        )
        if value.get("protocolVersion") != "2025-11-25":
            raise BakeoffFailure("BAKEOFF_PROTOCOL_VERSION")
        self._rpc("notifications/initialized", notification=True)
        actual = self._rpc("tools/list").get("tools")
        if not isinstance(actual, list):
            raise BakeoffFailure("BAKEOFF_SCHEMA_DRIFT")
        selected = [t for t in actual if isinstance(t, dict) and t.get("name") in ALLOWED]
        projected = [
            {"name": t["name"], "inputSchema": argument_schema(t.get("inputSchema"))}
            for t in selected
        ]
        if (
            len(projected) != len(ALLOWED)
            or len({t["name"] for t in projected}) != len(ALLOWED)
            or sorted(projected, key=lambda t: t["name"])
            != sorted(pinned_tools(), key=lambda t: t["name"])
        ):
            raise BakeoffFailure("BAKEOFF_SCHEMA_DRIFT")
        self.check_role()
        self.ready = True
        return self

    def _structured(self, result, change):
        value = result.get("structuredContent")
        if not isinstance(value, dict):
            raise BakeoffFailure("BAKEOFF_PROTOCOL_ERROR", unknown=change)
        status = value.get("http_status", 200)
        if type(status) is not int or not 100 <= status <= 599:
            raise BakeoffFailure("BAKEOFF_PROTOCOL_ERROR", unknown=change)
        if status >= 400:
            raise failure_status(status, change)
        if result.get("isError"):
            raise BakeoffFailure("BAKEOFF_TOOL_ERROR", status, change)
        return value

    def call(self, name, arguments, run_id):
        if name not in ALLOWED:
            raise BakeoffFailure("BAKEOFF_TOOL_DISABLED")
        if not self.ready:
            raise BakeoffFailure("BAKEOFF_NOT_READY")
        change = name in CHANGES
        if change and self.check_role() != "STAFF":
            raise BakeoffFailure("BAKEOFF_STAFF_REQUIRED", 403)
        args = copy.deepcopy(arguments)
        if change:
            args["idempotency_key"] = "axiom:" + run_id
        schema = next(t["inputSchema"] for t in pinned_tools() if t["name"] == name)
        if list(Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(args)):
            raise BakeoffFailure("BAKEOFF_ARGUMENTS", 422)
        result = self._rpc("tools/call", {"name": name, "arguments": args}, change=change)
        value = self._structured(result, change)
        receipt = value.get("receipt_id")
        receipt = (
            receipt
            if isinstance(receipt, str) and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", receipt)
            else None
        )
        outcome = value.get("outcome", "read")
        outcome = (
            outcome
            if outcome
            in {
                "allowed",
                "denied",
                "pending",
                "approved",
                "replayed",
                "invalid",
                "expired",
                "read",
            }
            else "unrecognized"
        )
        return {
            "http_status": value.get("http_status", 200),
            "outcome": outcome,
            "receipt_id": receipt,
            "replayed": value.get("replayed") is True,
            "untrusted_payload": value,
        }

    def close(self):
        self.client.close()
