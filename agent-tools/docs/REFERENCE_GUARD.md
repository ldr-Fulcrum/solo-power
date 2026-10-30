# Reference guard

A small separate process, intended to react when the agent's heartbeat lapses.

## Status

Not yet active against AXIOM. The guard is written and tested against a stand-in for AXIOM's routes. The build of AXIOM in this repository has no lock route, no human re-arm route and no event feed, so today the guard has nothing to talk to. What AXIOM does provide today is the runtime status: UNAVAILABLE when no health report has arrived inside the 120-second window, DEGRADED while reports arrive.

## What it is designed to do

1. Poll AXIOM's runtime status.
2. When AXIOM reports the agent UNAVAILABLE, record an event and ask AXIOM to lock the agent's proposals.
3. Keep a marker file. The lock stays until a human re-arms it in AXIOM, with a note. The guard never re-arms. If it sees that a human did, it records that.
4. Read AXIOM's own event feed (refusals, lapse, lock, re-arm) and record the events in its log. Events never cause a lock by themselves.

## Honest limits

- The reference guard is not fail-closed. If the guard process stops, nothing here locks anything.
- It does not change the sandbox. It acts on AXIOM's record of the agent only.
- It depends on AXIOM routes that are not in this repository's AXIOM build yet.

## Running it (once the AXIOM routes exist)

Set these in the environment of the operator (not the agent):

| Variable | Meaning |
|---|---|
| `GUARD_AXIOM_URL` | AXIOM base URL |
| `GUARD_TOKEN_ENV` | name of the variable that holds the guard's token (the token itself is never written to a file or a log) |
| `GUARD_STATE_DIR` | folder for the marker file, the cursor and the event log |
| `GUARD_POLL_SECONDS` | poll interval, default 10 |
| `GUARD_COMMAND` | optional; see below |

Then run `python -m reference_guard.guard`.

## Extension point

An operator can plug in a stricter guard. If `GUARD_COMMAND` is set, the guard runs that command each time it records an event, with the event as JSON on standard input. The command lives outside this repository and is configured by the operator. Event types: `heartbeat_lapsed`, `lock_requested`, `lock_failed`, `rearmed_by_human`, `status_unreachable`, `axiom_event`, `feed_unavailable`, each with `at`, `type` and `detail`.
