"""Hermes shell-hook adapter: journals every tool call (spec section 7).

Hermes runs the command for pre_tool_call and post_tool_call events and sends one JSON object on stdin:
  {hook_event_name, tool_name, tool_input, session_id, cwd, extra}
A pre_tool_call hook blocks the call by exiting 2 and printing {"decision":"block","reason":...}.

Modes
  observe  journal every call; anchoring is best effort; never blocks because of an anchor problem
  gate     the entry is journaled and anchored BEFORE the tool runs; if either fails, the call is blocked

Arguments and results are hashed. They are never stored, printed or logged.
Configure with environment variables (set by the operator, outside the agent's reach):
  EJ_JOURNAL     path to the journal file
  EJ_JOURNAL_ID  journal id for a new journal
  EJ_MODE        observe | gate        (default observe)
  EJ_ANCHOR_FILE path of a host-side anchor file       (or)
  EJ_ANCHOR_URL  AXIOM base URL, with EJ_ANCHOR_TOKEN_ENV naming the variable that holds the token
"""
import json
import os
import sys

from .anchor import AnchorError, AxiomAnchorClient, FileAnchorStore
from .canonical import sha256_hex, value_hash
from .journal import Journal, JournalCorrupt


def _anchor_target(env):
    if env.get("EJ_ANCHOR_FILE"):
        return FileAnchorStore(env["EJ_ANCHOR_FILE"])
    if env.get("EJ_ANCHOR_URL"):
        return AxiomAnchorClient(env["EJ_ANCHOR_URL"], env.get("EJ_ANCHOR_TOKEN_ENV", "EJ_ANCHOR_TOKEN"))
    return None


def _call_id(payload):
    extra = payload.get("extra") or {}
    cid = extra.get("tool_call_id") or extra.get("call_id")
    if cid:
        return str(cid)
    basis = [payload.get("session_id", ""), extra.get("turn_id", ""), payload.get("tool_name", ""),
             value_hash(payload.get("tool_input"))]
    return "derived-" + sha256_hex("|".join(map(str, basis)).encode("utf-8"))[:32]


def _outcome(extra):
    status = str((extra or {}).get("status", "")).lower()
    if status in ("ok", "success", "completed"):
        return "OK"
    if status in ("blocked", "denied"):
        return "BLOCKED"
    return "ERROR" if (status or (extra or {}).get("error_type")) else "OK"


def handle(payload, env, anchor_target=None, journal_factory=Journal):
    """Return (exit_code, stdout_text). Pure enough to test without Hermes."""
    mode = env.get("EJ_MODE", "observe")
    event = payload.get("hook_event_name", "")
    tool = str(payload.get("tool_name", ""))
    call_id = _call_id(payload)
    anchor = anchor_target if anchor_target is not None else _anchor_target(env)
    try:
        journal = journal_factory(env["EJ_JOURNAL"], env.get("EJ_JOURNAL_ID") or "journal-1")
        if event == "pre_tool_call":
            entry = journal.reserve(tool, payload.get("tool_input"), call_id)
        elif event == "post_tool_call":
            extra = payload.get("extra") or {}
            entry = journal.seal(tool, call_id, _outcome(extra), extra.get("result"))
        else:
            return 0, ""
        if anchor is not None:
            try:
                anchor.post(entry["journal_id"], entry["seq"], entry["entry_hash"])
            except AnchorError as exc:
                if mode == "gate" and event == "pre_tool_call":
                    return 2, json.dumps({"decision": "block", "reason": "journal anchor failed: %s" % exc})
        elif mode == "gate" and event == "pre_tool_call":
            return 2, json.dumps({"decision": "block", "reason": "gate mode needs an anchor target"})
        return 0, ""
    except (JournalCorrupt, OSError, KeyError, ValueError) as exc:
        if event == "pre_tool_call" and mode == "gate":
            return 2, json.dumps({"decision": "block", "reason": "journal unavailable: %s" % type(exc).__name__})
        sys.stderr.write("effect_journal hook error: %s\n" % type(exc).__name__)
        return 1, ""


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except ValueError:
        sys.stderr.write("effect_journal hook: stdin was not JSON\n")
        return 1
    code, out = handle(payload, os.environ)
    if out:
        sys.stdout.write(out)
    return code


if __name__ == "__main__":
    sys.exit(main())
