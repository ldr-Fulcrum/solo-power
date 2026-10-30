import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from reference_guard.guard import AxiomClient, Guard, GuardError


class FakeAxiom:
    """Stands in for the AXIOM routes the guard uses."""

    def __init__(self):
        self.status = "DEGRADED"
        self.locked = False
        self.lock_calls = []
        self.fail_lock = False
        self.fail_status = False
        self.events = []
        self.fail_events = False

    def get_status(self):
        if self.fail_status:
            raise GuardError("down")
        return {"status": self.status, "locked": self.locked}

    def get_events(self, since, limit=100):
        if self.fail_events:
            raise GuardError("no feed")
        return {"items": [e for e in self.events if e["id"] > since], "next_since": since, "limit": limit}

    def lock(self, reason):
        if self.fail_lock:
            raise GuardError("lock refused")
        self.lock_calls.append(reason)
        self.locked = True
        return {}


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.axiom = FakeAxiom()
        self.guard = Guard(self.axiom, self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def events(self):
        path = self.guard.events_path
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(line)["type"] for line in f if line.strip()]

    def test_healthy_agent_causes_no_action(self):
        self.assertIsNone(self.guard.poll_once())
        self.assertEqual(self.events(), [])
        self.assertFalse(self.guard.marked)

    def test_lapse_records_event_markeres_and_asks_for_lock(self):
        self.axiom.status = "UNAVAILABLE"
        self.assertEqual(self.guard.poll_once(), "lock_requested")
        self.assertEqual(self.events(), ["heartbeat_lapsed", "lock_requested"])
        self.assertEqual(self.axiom.lock_calls, ["heartbeat_lapsed"])
        self.assertTrue(self.guard.marked)

    def test_lock_stays_after_the_heartbeat_returns(self):
        self.axiom.status = "UNAVAILABLE"
        self.guard.poll_once()
        self.guard.poll_once()  # AXIOM now reports locked
        self.axiom.status = "DEGRADED"  # heartbeat is back
        self.assertIsNone(self.guard.poll_once())
        self.assertTrue(self.axiom.locked)
        self.assertTrue(self.guard.marked)
        self.assertEqual(len(self.axiom.lock_calls), 1)  # the guard never un-locks and never re-locks needlessly

    def test_only_a_human_rearm_clears_the_marker_and_the_guard_just_observes_it(self):
        self.axiom.status = "UNAVAILABLE"
        self.guard.poll_once(); self.guard.poll_once()
        self.axiom.status = "DEGRADED"
        self.axiom.locked = False  # a human re-armed in AXIOM
        self.assertEqual(self.guard.poll_once(), "rearmed_by_human")
        self.assertFalse(self.guard.marked)
        self.assertEqual(len(self.axiom.lock_calls), 1)

    def test_failed_lock_is_retried_and_a_returning_heartbeat_does_not_count_as_rearm(self):
        self.axiom.status = "UNAVAILABLE"
        self.axiom.fail_lock = True
        self.assertEqual(self.guard.poll_once(), "lock_failed")
        self.axiom.status = "DEGRADED"  # heartbeat returns before the lock took
        self.axiom.fail_lock = False
        self.assertEqual(self.guard.poll_once(), "lock_requested")
        self.assertTrue(self.axiom.locked)
        self.assertEqual(self.events().count("heartbeat_lapsed"), 1)

    def test_unreachable_axiom_is_recorded_and_takes_no_lock_action(self):
        self.axiom.fail_status = True
        self.assertEqual(self.guard.poll_once(), "status_unreachable")
        self.assertEqual(self.axiom.lock_calls, [])

    def test_restart_keeps_the_marker(self):
        self.axiom.status = "UNAVAILABLE"
        self.axiom.fail_lock = True
        self.guard.poll_once()
        restarted = Guard(self.axiom, self.dir.name)
        self.assertTrue(restarted.marked)
        self.axiom.status = "DEGRADED"; self.axiom.fail_lock = False
        self.assertEqual(restarted.poll_once(), "lock_requested")

    def test_extension_command_receives_each_event(self):
        calls = []
        g = Guard(self.axiom, self.dir.name, command="operator-hook",
                  run=lambda cmd, **kw: calls.append((cmd, json.loads(kw["input"]))))
        self.axiom.status = "UNAVAILABLE"
        g.poll_once()
        self.assertEqual([c[1]["type"] for c in calls], ["heartbeat_lapsed", "lock_requested"])
        self.assertTrue(all(c[0] == "operator-hook" for c in calls))


class FeedTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.axiom = FakeAxiom()
        self.guard = Guard(self.axiom, self.dir.name)

    def tearDown(self):
        self.dir.cleanup()

    def logged(self):
        with open(self.guard.events_path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_records_new_events_once_and_resumes_after_restart(self):
        self.axiom.events = [{"id": 1, "at": "t", "type": "proposal_refused", "code": "RUNTIME_STALE", "agent_id": "a", "tool": "task.create"},
                             {"id": 2, "at": "t", "type": "agent_locked", "code": None, "agent_id": "a", "tool": None}]
        self.assertEqual(self.guard.poll_events(), 2)
        self.guard.poll_events()
        self.assertEqual(len([e for e in self.logged() if e["type"] == "axiom_event"]), 2)
        restarted = Guard(self.axiom, self.dir.name)
        self.axiom.events.append({"id": 3, "at": "t", "type": "agent_rearmed", "code": None, "agent_id": "a", "tool": None})
        restarted.poll_events()
        self.assertEqual(len([e for e in self.logged() if e["type"] == "axiom_event"]), 3)

    def test_events_never_cause_a_lock(self):
        self.axiom.events = [{"id": 1, "at": "t", "type": "proposal_refused", "code": "AUTO_RATE_LIMIT", "agent_id": "a", "tool": "x"}]
        self.guard.poll_events()
        self.assertEqual(self.axiom.lock_calls, [])
        self.assertFalse(self.guard.marked)

    def test_missing_feed_is_recorded_once(self):
        self.axiom.fail_events = True
        self.guard.poll_events(); self.guard.poll_events()
        self.assertEqual([e["type"] for e in self.logged()].count("feed_unavailable"), 1)

    def test_only_identifier_fields_are_kept(self):
        self.axiom.events = [{"id": 1, "at": "t", "type": "proposal_refused", "code": "C", "agent_id": "a", "tool": "x",
                              "arguments": {"secret": "CANARY-feed"}}]
        self.guard.poll_events()
        with open(self.guard.events_path, "rb") as f:
            self.assertNotIn(b"CANARY-feed", f.read())


class StubHandler(BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        StubHandler.seen.append(("GET", self.path, self.headers.get("Authorization")))
        self._send({"status": "UNAVAILABLE", "locked": False})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        StubHandler.seen.append(("POST", self.path, json.loads(self.rfile.read(n))))
        self._send({})


class ClientTests(unittest.TestCase):
    def test_client_talks_to_the_documented_routes(self):
        srv = HTTPServer(("127.0.0.1", 0), StubHandler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        os.environ["GUARD_TEST_TOKEN"] = "tok-guard"
        try:
            c = AxiomClient("http://127.0.0.1:%d" % srv.server_port, "GUARD_TEST_TOKEN")
            self.assertEqual(c.get_status()["status"], "UNAVAILABLE")
            c.lock("heartbeat_lapsed")
            c.get_events(7)
        finally:
            srv.shutdown(); srv.server_close(); os.environ.pop("GUARD_TEST_TOKEN", None)
        self.assertEqual(StubHandler.seen[0], ("GET", "/api/v1/agent/status", "Bearer tok-guard"))
        self.assertEqual(StubHandler.seen[1], ("POST", "/api/v1/runtime/lock", {"reason": "heartbeat_lapsed"}))
        self.assertEqual(StubHandler.seen[2][:2], ("GET", "/api/v1/runtime/events?since=7&limit=100"))

    def test_missing_token_is_an_error_not_a_request(self):
        os.environ.pop("GUARD_TEST_TOKEN", None)
        with self.assertRaises(GuardError):
            AxiomClient("http://127.0.0.1:1", "GUARD_TEST_TOKEN").get_status()


if __name__ == "__main__":
    unittest.main()
