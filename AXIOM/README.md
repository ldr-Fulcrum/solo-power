# AXIOM application

This application provides persistent work, human approvals, receipts and a guarded runtime interface. Use Python 3.12+ and the application directory name AXIOM. This candidate is prepared for review; it is unsigned and inactive.

## Core mode: no optional service needed

Leave DARLA_BAKEOFF_URL unset. No optional task-hub adapter tools are registered, no adapter startup checks or connections occur, and no adapter credential is needed. The core application remains usable with its local tools.

In a separate runtime or review copy, install the locked dependencies and explicitly select synthetic mode before starting the application:

```powershell
uv sync --frozen
$env:DARLA_SYNTHETIC_MODE='1'
uv run python -B -m backend.app.serve
```

The default bind is loopback. State is local to that runtime copy and excluded from source. The existing synthetic sessions are development fixtures, not production sign-in. On empty state, an authorized human may use POST /api/v1/runtime/bootstrap with a nonblank note and a bounded duration. Bootstrap is labeled HUMAN_ATTESTED, never verified runtime evidence, and cannot clear a lock. Status is GET /api/v1/agent/status. Ordinary runtime health later supersedes bootstrap.

## Optional task-hub adapter

The adapter id is bakeoff. It remains off until DARLA_BAKEOFF_URL is explicitly set before process startup. To exercise it, use a separately supplied service or the judge stand-in provided by the runtime lane. This build does not contain or launch that stand-in. Credentials, when required, come from the process variable named by DARLA_BAKEOFF_TOKEN_ENV and are not stored in source or state.

See integration/OPTIONAL_TASK_HUB_ADAPTER_v1.md. The pin in contracts/task_hub/tool_arguments_v1.json contains only tool names and argument validation schemas. Descriptions are original application text. There is no external implementation, documentation, credential format or private location in this adapter package.

Six read tools are AUTO; four change tools are ASK. A human must approve changes, and STAFF role is required. Approval does not override service permissions. Results are untrusted data. Unknown change outcomes remain UNRESOLVED and are not automatically retried.

The optional integration test is read-only and is skipped unless DARLA_BAKEOFF_URL is explicitly set in that test process. Normal author validation uses the in-process fixture without real credentials. The source manifest is reports/G1_BACKEND_MANIFEST_007_A1.json. Review evidence and compiled output are separate; nothing here authorizes activation or publication.
