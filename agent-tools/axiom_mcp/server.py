"""AXIOM tool server for Model Context Protocol clients (stdio, newline-delimited JSON-RPC 2.0).

It is the agent's only way to ask for effects: every tool is a PROPOSAL sent to AXIOM, which applies its own
rules (approval tiers, rate cap, stale-heartbeat gate where present) and its own receipts. This server decides
nothing and approves nothing. It lists the tools AXIOM itself lists (GET /api/v1/runtime/tools), so a tool
AXIOM adds later appears here without a code change.

Configuration is by environment variable, set by the operator outside the agent's reach:
  AXIOM_URL         base URL (default http://host.openshell.internal:8765, the sandbox's route to the host)
  AXIOM_TOKEN_ENV   NAME of the variable that holds the agent's bearer (default AXIOM_AGENT_TOKEN).
                    The token is read at call time and is never printed, logged or returned.
  AXIOM_AGENT_ID, AXIOM_PRINCIPAL_ID, AXIOM_SESSION_ID, AXIOM_DOMAIN
                    identity fields of the proposal; defaults are the synthetic development fixture.
The synthetic fixture credential is for local development only; it is not production authentication.
"""
import datetime
import json
import os
import sys
import urllib.error
import urllib.request
import uuid

SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18", "2025-03-26")
SYNTHETIC_SCOPES = ("read", "runtime.propose", "run.write", "task.write")
SUMMARY_PROPERTY = {"type": "string", "minLength": 1, "maxLength": 300,
                    "description": "One sentence a person will read when asked to approve this. Optional."}


def tool_name(axiom_name):
    return axiom_name.replace(".", "_")


class AxiomTools:
    def __init__(self, env=None, opener=None):
        env = os.environ if env is None else env
        self.base = env.get("AXIOM_URL", "http://host.openshell.internal:8765").rstrip("/")
        self.token_env = env.get("AXIOM_TOKEN_ENV", "AXIOM_AGENT_TOKEN")
        self.env = env
        self.agent_id = env.get("AXIOM_AGENT_ID", "synthetic-darla-agent")
        self.principal_id = env.get("AXIOM_PRINCIPAL_ID", "synthetic-dani")
        self.session_id = env.get("AXIOM_SESSION_ID", "synthetic-agent-bakery")
        self.domain = env.get("AXIOM_DOMAIN", "bakery")
        self._open = opener or urllib.request.urlopen
        self._registry = None

    # -- AXIOM access -----------------------------------------------------------------------------
    def _request(self, method, path, body=None, headers=None):
        token = self.env.get(self.token_env, "")
        if not token:
            raise AxiomUnavailable("the token variable %s is not set" % self.token_env)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json", **(headers or {})})
        try:
            with self._open(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            try:
                payload = json.loads(exc.read().decode("utf-8") or "{}")
            except ValueError:
                payload = {}
            return exc.code, payload
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise AxiomUnavailable("AXIOM could not be reached (%s)" % type(exc).__name__) from exc

    def registry(self):
        if self._registry is None:
            status, payload = self._request("GET", "/api/v1/runtime/tools")
            if status != 200 or not isinstance(payload, dict):
                raise AxiomUnavailable("AXIOM refused the tool list (HTTP %s)" % status)
            self._registry = payload
        return self._registry

    # -- MCP surface ------------------------------------------------------------------------------
    def list_tools(self):
        tools = []
        for name, spec in sorted(self.registry().items()):
            schema = json.loads(json.dumps(spec.get("arguments", {"type": "object"})))
            schema.setdefault("properties", {})["summary"] = SUMMARY_PROPERTY
            tools.append({
                "name": tool_name(name),
                "description": "Ask AXIOM to run %s. This sends a proposal; AXIOM applies its own approval tier "
                               "(%s) and records a receipt. Effect: %s." % (name, spec.get("tier", "?"), spec.get("effect", "?")),
                "inputSchema": schema,
                "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False},
            })
        return tools

    def call(self, mcp_name, arguments):
        """Return (is_error, result_dict)."""
        by_mcp = {tool_name(n): n for n in self.registry()}
        axiom_name = by_mcp.get(mcp_name)
        if axiom_name is None:
            return True, {"error": "unknown_tool", "message": "AXIOM does not offer a tool named %s" % mcp_name}
        args = dict(arguments)
        summary = args.pop("summary", None) or ("Agent asks AXIOM to run %s" % axiom_name)
        spec = self.registry()[axiom_name]
        request_id = "mcp-" + uuid.uuid4().hex[:24]
        expires = (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=5)).isoformat()
        scopes = sorted(set(SYNTHETIC_SCOPES) | {spec.get("scope", "read")})
        body = {
            "input": {
                "principal_id": self.principal_id, "agent_id": self.agent_id, "request_id": request_id,
                "goal": summary[:2000], "domain": self.domain, "tool_allowlist": [axiom_name],
                "authorization": {"principal_id": self.principal_id, "session_id": self.session_id,
                                  "domain": self.domain, "permitted_scopes": scopes, "expires_at": expires},
                "budget": {"max_tokens": 100, "max_tool_calls": 1, "max_cost_usd": "0"},
                "deadline": expires,
            },
            "result": {
                "request_id": request_id, "actor_id": self.agent_id, "test_double": False,
                "action": {"requested_tool": axiom_name, "parameters": args, "summary": summary[:300],
                           "provenance_references": ["mcp:axiom_mcp", "request:" + request_id]},
            },
        }
        try:
            status, payload = self._request("POST", "/api/v1/runtime/proposals", body, {"Idempotency-Key": request_id})
        except AxiomUnavailable as exc:
            return True, {"error": "axiom_unavailable", "message": str(exc)}
        if status in (200, 201):
            run_status = payload.get("status")
            return False, {
                "outcome": "waiting_for_human_approval" if run_status == "BLOCKED" else "accepted",
                "run_id": payload.get("id"), "status": run_status, "approval_id": payload.get("approval_id"),
                "tier": spec.get("tier"), "request_id": request_id,
            }
        err = payload.get("error", {}) if isinstance(payload, dict) else {}
        return True, {"error": str(err.get("code", "refused")), "http_status": status,
                      "message": str(err.get("message", "AXIOM refused the proposal"))[:300], "request_id": request_id}


class AxiomUnavailable(Exception):
    pass


def handle(tools, message):
    """One JSON-RPC message in, a response dict (or None for a notification) out."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "not a valid JSON-RPC 2.0 request (batches are not supported)")
    mid = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if "id" not in message:
        return None  # notification
    if method == "initialize":
        wanted = params.get("protocolVersion")
        version = wanted if wanted in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return _ok(mid, {"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                         "serverInfo": {"name": "axiom-tools", "version": "1"}})
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        try:
            return _ok(mid, {"tools": tools.list_tools()})
        except AxiomUnavailable as exc:
            return _error(mid, -32603, str(exc))
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error(mid, -32602, "tools/call needs a tool name and an object of arguments")
        try:
            is_error, result = tools.call(name, arguments)
        except AxiomUnavailable as exc:
            is_error, result = True, {"error": "axiom_unavailable", "message": str(exc)}
        return _ok(mid, {"content": [{"type": "text", "text": json.dumps(result, sort_keys=True)}],
                         "structuredContent": result, "isError": is_error})
    return _error(mid, -32601, "method not found: %s" % method)


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main(stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    tools = AxiomTools()
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            response = _error(None, -32700, "invalid JSON")
        else:
            response = handle(tools, message)
        if response is not None:
            stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
