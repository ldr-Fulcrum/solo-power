"""Reference guard: a small separate process that reacts when the agent's heartbeat lapses.

Status: not yet active against AXIOM. The routes it uses (lock, re-arm, event feed) are not in the AXIOM build
in this repository; it is tested against a stand-in.

What it does
  1. Polls AXIOM's runtime status (GET /api/v1/agent/status).
  2. If AXIOM reports the agent UNAVAILABLE (heartbeat lapse), it records an event and asks AXIOM to
     lock the agent's proposals (POST /api/v1/runtime/lock).
  3. It keeps a marker file. The lock stays until a human re-arms it in AXIOM. The guard never re-arms.

What it does not do
  It does not stop or restrict the sandbox, and it is not fail-closed: if the guard process stops,
  nothing here locks. AXIOM's own refusal of stale proposals is the baseline; this guard adds the marker,
  the event record and the human re-arm requirement.

Extension point: set GUARD_COMMAND to run an operator-supplied command with the event JSON on stdin each time an event is
recorded. The repository ships no such command.

Configuration is by environment variable (set by the operator): GUARD_AXIOM_URL, GUARD_TOKEN_ENV (name of the
variable holding the guard's token), GUARD_STATE_DIR, GUARD_POLL_SECONDS (default 10), GUARD_COMMAND (optional).
"""
import datetime
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request


class GuardError(Exception):
    pass


def utc_now():
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (now.microsecond // 1000)


class AxiomClient:
    def __init__(self, base_url, token_env, timeout=5.0):
        self.base = base_url.rstrip("/")
        self.token_env = token_env
        self.timeout = timeout

    def _call(self, method, path, body=None):
        token = os.environ.get(self.token_env, "")
        if not token:
            raise GuardError("token environment variable %s is not set" % self.token_env)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": "Bearer " + token, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise GuardError("request to %s failed: %s" % (path, type(exc).__name__)) from exc

    def get_status(self):
        return self._call("GET", "/api/v1/agent/status")

    def lock(self, reason):
        return self._call("POST", "/api/v1/runtime/lock", {"reason": reason})

    def get_events(self, since, limit=100):
        return self._call("GET", "/api/v1/runtime/events?since=%d&limit=%d" % (since, limit))


class Guard:
    def __init__(self, client, state_dir, command=None, clock=utc_now, run=subprocess.run):
        self.client = client
        self.state_dir = state_dir
        self.command = command
        self.clock = clock
        self._run = run
        os.makedirs(state_dir, exist_ok=True)
        self.marker_path = os.path.join(state_dir, "guard.marker")
        self.events_path = os.path.join(state_dir, "guard-events.jsonl")
        self.cursor_path = os.path.join(state_dir, "guard.cursor")
        self._feed_down = False

    @property
    def marked(self):
        return os.path.exists(self.marker_path)

    def poll_once(self):
        """One poll. Returns the event type recorded, or None."""
        try:
            status = self.client.get_status()
        except GuardError as exc:
            return self._event("status_unreachable", str(exc))
        locked = bool(status.get("locked"))
        state = self._marker_state()
        if locked:
            if state != "confirmed":
                self._write_marker("confirmed")
            return None
        if state == "confirmed":
            os.remove(self.marker_path)  # a human re-armed in AXIOM; the guard only observes it
            return self._event("rearmed_by_human", "AXIOM reports the lock cleared")
        if state == "requested" or status.get("status") == "UNAVAILABLE":
            if state is None:
                self._event("heartbeat_lapsed", "AXIOM reports the agent UNAVAILABLE")
                self._write_marker("requested")
            try:
                self.client.lock("heartbeat_lapsed")
            except GuardError as exc:
                return self._event("lock_failed", str(exc))
            return self._event("lock_requested", "asked AXIOM to lock proposals")
        return None

    def poll_events(self):
        """Record AXIOM's own events (refusals, lapse, lock, re-arm) in the guard's event log. Takes no action.

        Returns the number of events recorded. A feed that is missing or unreachable is recorded once per run.
        """
        cursor = self._read_cursor()
        try:
            batch = self.client.get_events(cursor)
        except GuardError as exc:
            if not self._feed_down:
                self._feed_down = True
                self._event("feed_unavailable", str(exc))
            return 0
        self._feed_down = False
        events = batch.get("items", []) if isinstance(batch, dict) else []
        for ev in events:
            if not isinstance(ev.get("id"), int) or ev["id"] <= cursor:
                continue
            self._event("axiom_event", json.dumps(
                {k: ev.get(k) for k in ("id", "at", "type", "code", "agent_id", "tool")}, sort_keys=True))
            cursor = ev["id"]
            self._write_cursor(cursor)
        return len(events)

    def _read_cursor(self):
        try:
            with open(self.cursor_path, "r", encoding="utf-8") as f:
                return int(f.read().strip() or 0)
        except (FileNotFoundError, ValueError):
            return 0

    def _write_cursor(self, value):
        tmp = self.cursor_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(str(value))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.cursor_path)

    def _marker_state(self):
        try:
            with open(self.marker_path, "r", encoding="utf-8") as f:
                return json.load(f).get("state")
        except FileNotFoundError:
            return None
        except ValueError:
            return "requested"  # unreadable marker: stay marked

    def _write_marker(self, state):
        tmp = self.marker_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"state": state, "at": self.clock()}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.marker_path)

    def _event(self, kind, detail):
        rec = {"at": self.clock(), "type": kind, "detail": detail}
        with open(self.events_path, "ab") as f:
            f.write((json.dumps(rec, sort_keys=True) + "\n").encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        if self.command:
            try:
                self._run(self.command, input=json.dumps(rec).encode("utf-8"), shell=True, timeout=30, check=False)
            except (OSError, subprocess.SubprocessError):
                pass
        return kind


def main():
    env = os.environ
    for key in ("GUARD_AXIOM_URL", "GUARD_TOKEN_ENV", "GUARD_STATE_DIR"):
        if not env.get(key):
            sys.stderr.write("missing %s\n" % key)
            return 2
    guard = Guard(AxiomClient(env["GUARD_AXIOM_URL"], env["GUARD_TOKEN_ENV"]), env["GUARD_STATE_DIR"],
                  env.get("GUARD_COMMAND"))
    interval = float(env.get("GUARD_POLL_SECONDS", "10"))
    while True:
        guard.poll_once()
        guard.poll_events()
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
