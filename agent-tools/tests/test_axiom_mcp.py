import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from axiom_mcp.server import AxiomTools, handle, tool_name

REGISTRY = {
    "task.create": {"arguments": {"type": "object", "additionalProperties": False, "required": ["title"],
                                  "properties": {"title": {"type": "string", "maxLength": 200}}},
                    "scope": "task.write", "tier": "AUTO", "effect": "LOCAL_ONLY"},
    "draft.create": {"arguments": {"type": "object", "additionalProperties": False, "required": ["description"],
                                   "properties": {"description": {"type": "string"}}},
                     "scope": "draft.write", "tier": "ASK", "effect": "LOCAL_ONLY"},
}
CANARY = "CANARY-token-5e1b"


class Stub(BaseHTTPRequestHandler):
    seen = []
    mode = "ok"

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        Stub.seen.append(("GET", self.path, self.headers.get("Authorization")))
        self._send(200, REGISTRY)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(n))
        Stub.seen.append(("POST", self.path, self.headers.get("Authorization"), self.headers.get("Idempotency-Key"), body))
        tool = body["result"]["action"]["requested_tool"]
        if Stub.mode == "locked":
            return self._send(423, {"error": {"code": "AGENT_LOCKED", "message": "locked until a human re-arms"}})
        if tool == "draft.create":
            return self._send(201, {"id": "run-2", "status": "BLOCKED", "approval_id": "appr-1"})
        self._send(201, {"id": "run-1", "status": "QUEUED"})


class ServerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = HTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.url = "http://127.0.0.1:%d" % cls.srv.server_port

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def setUp(self):
        Stub.seen = []; Stub.mode = "ok"
        self.env = {"AXIOM_URL": self.url, "AXIOM_TOKEN_ENV": "T_AXIOM", "T_AXIOM": CANARY}
        self.tools = AxiomTools(self.env)

    def rpc(self, method, params=None, mid=1):
        return handle(self.tools, {"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})


class ProtocolTests(ServerCase):
    def test_initialize_negotiates_and_declares_only_tools(self):
        r = self.rpc("initialize", {"protocolVersion": "2025-06-18"})["result"]
        self.assertEqual(r["protocolVersion"], "2025-06-18")
        self.assertEqual(list(r["capabilities"]), ["tools"])
        self.assertEqual(self.rpc("initialize", {"protocolVersion": "1999-01-01"})["result"]["protocolVersion"], "2025-11-25")

    def test_notification_gets_no_reply_and_batch_is_rejected(self):
        self.assertIsNone(handle(self.tools, {"jsonrpc": "2.0", "method": "notifications/initialized"}))
        self.assertEqual(handle(self.tools, [{"jsonrpc": "2.0", "id": 1, "method": "ping"}])["error"]["code"], -32600)

    def test_unknown_method_and_bad_call_arguments(self):
        self.assertEqual(self.rpc("resources/list")["error"]["code"], -32601)
        self.assertEqual(self.rpc("tools/call", {"name": "task_create", "arguments": "no"})["error"]["code"], -32602)

    def test_tools_list_mirrors_axiom_registry_with_an_optional_summary(self):
        tools = self.rpc("tools/list")["result"]["tools"]
        self.assertEqual([t["name"] for t in tools], ["draft_create", "task_create"])
        schema = tools[1]["inputSchema"]
        self.assertIn("summary", schema["properties"])
        self.assertNotIn("summary", schema.get("required", []))
        self.assertIn("AUTO", tools[1]["description"])
        self.assertEqual(tool_name("plan.create"), "plan_create")


class CallTests(ServerCase):
    def call(self, name, args):
        return self.rpc("tools/call", {"name": name, "arguments": args})["result"]

    def test_call_sends_a_complete_proposal_with_an_idempotency_key(self):
        res = self.call("task_create", {"title": "Prep dough", "summary": "Add a prep reminder"})
        self.assertFalse(res["isError"])
        self.assertEqual(res["structuredContent"]["outcome"], "accepted")
        post = [s for s in Stub.seen if s[0] == "POST"][0]
        body = post[4]
        self.assertEqual(post[1], "/api/v1/runtime/proposals")
        self.assertEqual(post[3], body["input"]["request_id"])
        self.assertLessEqual(len(body["input"]["request_id"]), 90)
        self.assertEqual(body["result"]["action"]["requested_tool"], "task.create")
        self.assertEqual(body["result"]["action"]["parameters"], {"title": "Prep dough"})
        self.assertEqual(body["result"]["action"]["summary"], "Add a prep reminder")
        self.assertFalse(body["result"]["test_double"])
        self.assertEqual(body["input"]["tool_allowlist"], ["task.create"])
        self.assertIn("task.write", body["input"]["authorization"]["permitted_scopes"])
        self.assertEqual(body["input"]["budget"]["max_tool_calls"], 1)

    def test_default_summary_and_ask_tier_reports_waiting_for_a_human(self):
        res = self.call("draft_create", {"description": "Order flour"})
        self.assertEqual(res["structuredContent"]["outcome"], "waiting_for_human_approval")
        self.assertEqual(res["structuredContent"]["approval_id"], "appr-1")
        body = [s for s in Stub.seen if s[0] == "POST"][0][4]
        self.assertTrue(body["result"]["action"]["summary"].startswith("Agent asks AXIOM to run draft.create"))

    def test_each_call_gets_a_new_request_id(self):
        self.call("task_create", {"title": "a"}); self.call("task_create", {"title": "a"})
        ids = [s[4]["input"]["request_id"] for s in Stub.seen if s[0] == "POST"]
        self.assertEqual(len(set(ids)), 2)

    def test_axiom_refusal_is_reported_as_an_error_with_its_code(self):
        Stub.mode = "locked"
        res = self.call("task_create", {"title": "x"})
        self.assertTrue(res["isError"])
        self.assertEqual(res["structuredContent"]["error"], "AGENT_LOCKED")
        self.assertEqual(res["structuredContent"]["http_status"], 423)

    def test_unknown_tool_is_a_tool_error_and_sends_nothing(self):
        res = self.call("shell_exec", {"cmd": "ls"})
        self.assertTrue(res["isError"])
        self.assertEqual([s for s in Stub.seen if s[0] == "POST"], [])

    def test_unreachable_axiom_and_missing_token_are_tool_errors_without_secrets(self):
        t = AxiomTools({"AXIOM_URL": "http://127.0.0.1:1", "AXIOM_TOKEN_ENV": "T_AXIOM", "T_AXIOM": CANARY})
        out = handle(t, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "task_create", "arguments": {}}})
        self.assertTrue(out["result"]["isError"])
        self.assertNotIn(CANARY, json.dumps(out))
        t2 = AxiomTools({"AXIOM_URL": self.url, "AXIOM_TOKEN_ENV": "T_MISSING"})
        out2 = handle(t2, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        self.assertIn("error", out2)
        self.assertNotIn("Bearer", json.dumps(out2))

    def test_token_goes_in_the_header_only_never_in_any_output(self):
        res = self.rpc("tools/call", {"name": "task_create", "arguments": {"title": "x"}})
        self.assertNotIn(CANARY, json.dumps(res))
        self.assertTrue(all(s[2] == "Bearer " + CANARY for s in Stub.seen))
        body = [s for s in Stub.seen if s[0] == "POST"][0][4]
        self.assertNotIn(CANARY, json.dumps(body))


class StdioTests(ServerCase):
    def test_real_process_over_stdio(self):
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env = {**os.environ, **self.env, "PYTHONPATH": here}
        msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-11-25"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "task_create", "arguments": {"title": "x"}}}]
        p = subprocess.run([sys.executable, "-B", "-m", "axiom_mcp.server"], input="\n".join(map(json.dumps, msgs)) + "\n",
                           capture_output=True, text=True, env=env, cwd=here, timeout=60)
        lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
        self.assertEqual([l["id"] for l in lines], [1, 2, 3])
        self.assertFalse(lines[2]["result"]["isError"])
        self.assertNotIn(CANARY, p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
