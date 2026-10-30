"""Registry integration; only configured, pin-checked tools are exposed."""

import copy
import os

from jsonschema import Draft202012Validator, FormatChecker

from backend.app.bakeoff_client import (
    ALLOWED,
    CHANGES,
    READS,
    Adapter,
    BakeoffFailure,
    pinned_tools,
)
from backend.app.bootstrap import Services as BootstrapServices
from backend.app.security import Fault, require
from backend.app.task_hub_descriptions import DESCRIPTIONS


class Arguments:
    def __init__(self, value):
        self.value = value

    def model_dump(self, **kwargs):
        return copy.deepcopy(self.value)


def schema_model(schema):
    class Pinned:
        @classmethod
        def model_json_schema(cls):
            return copy.deepcopy(schema)

        @classmethod
        def model_validate(cls, value):
            if not isinstance(value, dict) or list(
                Draft202012Validator(schema, format_checker=FormatChecker()).iter_errors(value)
            ):
                raise ValueError("Pinned schema mismatch")
            for k, v in value.items():
                if (
                    schema.get("properties", {}).get(k, {}).get("type") == "integer"
                    and type(v) is not int
                ):
                    raise ValueError("Strict integer required")
            return Arguments(value)

    return Pinned


class Services(BootstrapServices):
    def __init__(self, settings):
        super().__init__(settings)
        self.bakeoff = None
        self.bakeoff_url = os.environ.get("DARLA_BAKEOFF_URL")
        self.bakeoff_error = "BAKEOFF_NOT_CONFIGURED" if self.bakeoff_url else "DISABLED"
        try:
            adapter = Adapter.from_environment() if self.bakeoff_url else None
            if adapter:
                try:
                    adapter.start()
                except Exception:
                    adapter.close()
                    raise
                self.bakeoff = adapter
                self.bakeoff_error = ""
        except BakeoffFailure as error:
            self.bakeoff_error = error.code
        except Exception:
            self.bakeoff_error = "BAKEOFF_STARTUP_FAILED"
        self._bakeoff_schemas = {}

    def tool_registry(self):
        result = super().tool_registry().copy()
        if self.bakeoff_active():
            if not self._bakeoff_schemas:
                self._bakeoff_schemas = {
                    "bakeoff." + t["name"]: schema_model(t["inputSchema"])
                    for t in pinned_tools()
                    if t["name"] in ALLOWED
                }
            names = READS | (CHANGES if self.bakeoff.role == "STAFF" else set())
            for name in names:
                result["bakeoff." + name] = (
                    self._bakeoff_schemas["bakeoff." + name],
                    "read" if name in READS else "run.write",
                    "bakeoff_result",
                )
        return result

    def validate_tool(self, p, tool, args):
        if tool.startswith("bakeoff."):
            require(p, "read", bakery=True)
            if tool not in self.tool_registry():
                code = (
                    "BAKEOFF_STAFF_REQUIRED"
                    if self.bakeoff and tool.removeprefix("bakeoff.") in CHANGES
                    else self.bakeoff_error or "BAKEOFF_TOOL_DISABLED"
                )
                raise Fault(403, code, code)
        return super().validate_tool(p, tool, args)

    def tool_tier(self, tool):
        if tool.startswith("bakeoff."):
            return "AUTO" if tool.removeprefix("bakeoff.") in READS else "ASK"
        return super().tool_tier(tool)

    def tool_policy_version(self, tool):
        return (
            "darla-bakeoff-policy/1"
            if tool.startswith("bakeoff.")
            else super().tool_policy_version(tool)
        )

    def mutate(self, p, operation, key, payload, record_id=None):
        if operation == "run.cancel":
            require(p, "run.write")
            with self.store.transaction() as c:
                run = self._get(c, "run", record_id, p)
                if run["tool"].startswith("bakeoff.") and run["checkpoint"].get(
                    "external_attempt_started"
                ):
                    raise Fault(
                        409,
                        "BAKEOFF_ATTEMPT_IMMUTABLE",
                        "An external attempt must retain its outcome evidence",
                    )
                return super().mutate(p, operation, key, payload, record_id)
        return super().mutate(p, operation, key, payload, record_id)

    def runtime_event(self, p, event, key):
        require(p, "runtime.propose")
        if p.actor_kind != "agent":
            raise Fault(403, "AGENT_SESSION_REQUIRED", "Runtime events require an agent session")
        if not key or len(key) > 128:
            raise Fault(400, "IDEMPOTENCY_REQUIRED", "Runtime events require Idempotency-Key")
        with self.store.transaction() as c:
            run = self._get(c, "run", event.run_id, p)
            if run["tool"].startswith("bakeoff.") and run["checkpoint"].get(
                "external_attempt_started"
            ):
                raise Fault(
                    409,
                    "BAKEOFF_ATTEMPT_IMMUTABLE",
                    "Runtime events cannot rewrite an external attempt",
                )
            return super().runtime_event(p, event, key)

    def bakeoff_active(self):
        return bool(self.bakeoff_url and self.bakeoff and self.bakeoff.ready)

    def bakeoff_status(self):
        if not self.bakeoff_url:
            return "DISABLED"
        return (
            self.bakeoff.warning or "MCP_PINNED_STAFF"
            if self.bakeoff_active()
            else self.bakeoff_error
        )

    def runtime_tools(self, principal):
        result = super().runtime_tools(principal)
        for name, value in result.items():
            if name.startswith("bakeoff."):
                value["description"] = DESCRIPTIONS[name.removeprefix("bakeoff.")]
        return result

    def _receipt(self, c, p, action, body, key, outcome="COMPLETED", error=None):
        result = super()._receipt(c, p, action, body, key, outcome, error)
        if "bakeoff" in body:
            result = self._save(c, {**result, "bakeoff": body["bakeoff"]})
        return result
