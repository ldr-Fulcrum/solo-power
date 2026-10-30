# \# solo-power

# 

# Receipts and human approvals for an AI agent's tool calls.

# 

# solo-power has two parts:

# 

# \- \*\*AXIOM\*\* (`AXIOM/`): an application for persistent work, human approvals, receipts and a guarded runtime interface. Read tools run automatically. Change tools need a human to approve them, and outcomes that are unknown stay UNRESOLVED and are never retried automatically.

# \- \*\*Agent tools\*\* (`agent-tools/`): an effect journal, its verifier, a file-based anchor store, a reference guard and an MCP tool server for AXIOM.

# 

# \## Receipts for what the agent does

# 

# Every tool call the agent makes is written to an append-only, hash-chained journal before it runs, and sealed when it ends. A call that was started and never sealed is reported as unresolved, and is never retried automatically. Arguments and results are hashed and never written to the journal. The head of the chain can be written to an append-only anchor store outside the agent's reach, so edits, deletions and reordering are detectable, and truncation is detectable once an anchor is stored.

# 

# See `agent-tools/docs/EFFECT\_JOURNAL\_SPEC.md`. From `agent-tools/`:

# 

# ```

# python -m effect\_journal.verify <journal> --anchors <anchors.json>

# python -m unittest discover -s tests -t .

# ```

# 

# \## When the agent goes quiet

# 

# AXIOM reports the agent as UNAVAILABLE when no runtime health report has arrived inside its 120-second window, and as DEGRADED while reports arrive (runtime-reported, not verified by AXIOM).

# 

# An operator can plug in a stricter guard. See `agent-tools/docs/REFERENCE\_GUARD.md`.

# 

# \## Running AXIOM

# 

# Python 3.12+ and `uv`. From `AXIOM/`:

# 

# ```

# uv sync --frozen

# $env:DARLA\_SYNTHETIC\_MODE='1'

# uv run python -B -m backend.app.serve

# ```

# 

# It binds to loopback by default. Leave `DARLA\_BAKEOFF\_URL` unset for core mode, which needs no optional service. All data is synthetic. See `AXIOM/README.md` and `AXIOM/docs/`.

# 

# \## Honest limits

# 

# \- The journal, the verifier and the file-based anchor store work and are tested.

# \- An AXIOM-backed anchor store is not yet active against AXIOM. The build of AXIOM in this repository does not have the route it needs.

# \- The reference guard is not yet active against AXIOM, and it is not fail-closed: if the guard process itself stops, nothing here locks.

# \- The Hermes hook adapter is tested against the hook's documented payload. Coverage of every Hermes tool type has not been tested yet.

# \- The journal covers calls that reach the hook. It does not cover effects that bypass the hook, and it does not protect against someone who controls the machine holding both the journal and the anchors.

# 

# \## License

# 

# MIT. Copyright (c) 2026 Fulcrum Fortress Consulting, LLC. See `LICENSE`.

