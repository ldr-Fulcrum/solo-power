# AXIOM tool server

`axiom_mcp/server.py` is a small Model Context Protocol server (stdio, standard library only) that gives an agent AXIOM's governed tools and nothing else. It is how the Hermes agent asks for anything to happen.

## What it does

- Lists the tools AXIOM itself lists (`GET /api/v1/runtime/tools`). A tool AXIOM adds later appears without a code change. Names use underscores (`task_create` for `task.create`).
- Every call is a proposal sent to `POST /api/v1/runtime/proposals`. AXIOM applies its own approval tier (AUTO runs, ASK waits for a person), its own rate cap and its own receipts. This server approves nothing and decides nothing.
- Each call gets a new request id, which is also the idempotency key, so a retry is safe.
- Each tool has one optional extra argument, `summary`: one sentence a person reads when asked to approve.
- A refusal from AXIOM (for example a lock) comes back as a tool error with AXIOM's code. AXIOM being unreachable also comes back as a tool error.

## What it does not do

- It does not hold or show a credential. The bearer is read from the environment variable named by `AXIOM_TOKEN_ENV` at call time and goes only in the request header: never in a result, an error, a log or an argument (tests check this with a canary).
- It has no file, shell or network tool of its own. The agent's built-in tools are switched off in the Hermes configuration, so this is the single front.
- The synthetic fixture credential used in development is not production authentication.

## Configuration (set by the operator, outside the agent's reach)

| Variable | Default |
|---|---|
| `AXIOM_URL` | `http://host.openshell.internal:8765` (the sandbox's route to the host) |
| `AXIOM_TOKEN_ENV` | `AXIOM_AGENT_TOKEN` (the name of the variable holding the bearer) |
| `AXIOM_AGENT_ID`, `AXIOM_PRINCIPAL_ID`, `AXIOM_SESSION_ID`, `AXIOM_DOMAIN` | the synthetic development fixture |

Register it with Hermes: `hermes mcp add axiom --command <python> --env PYTHONPATH=<folder holding axiom_mcp> AXIOM_URL=... AXIOM_TOKEN_ENV=... <that variable>=... --args -m axiom_mcp.server`.

Tests: `python -m unittest discover -s tests -t .` (a stand-in AXIOM, so no running AXIOM is needed).
