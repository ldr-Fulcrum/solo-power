# README sections (for the repository README)

Two sections. Paste them into the README as they are, or link to them.

## Receipts for what the agent does

Every tool call the agent makes is written to an append-only, hash-chained journal before it runs, and sealed when it ends. A call that was started and never sealed is reported as unresolved, and is never retried automatically. Arguments and results are hashed and never written to the journal. The head of the chain can be written to an append-only anchor store outside the agent's reach, so edits, deletions and reordering are detectable, and truncation is detectable once an anchor is stored. See `docs/EFFECT_JOURNAL_SPEC.md`; verify a journal with `python -m effect_journal.verify <journal> --anchors <anchors.json>`; run the checks with `python -m unittest discover -s tests -t .`.

Status: the journal, the verifier and the file-based anchor store work and are tested. An AXIOM-backed anchor store is not yet active against AXIOM: the build of AXIOM in this repository does not have the route it needs. The Hermes hook adapter is tested against the hook's documented payload; coverage of every Hermes tool type has not been tested yet. The journal covers calls that reach the hook. It does not cover effects that bypass the hook, and it does not protect against someone who controls the machine holding both the journal and the anchors.

## When the agent goes quiet

AXIOM reports the agent as UNAVAILABLE when no runtime health report has arrived inside its 120-second window, and as DEGRADED while reports arrive (runtime-reported, not verified by AXIOM).

This repository also contains a reference guard, a small separate process. It is not yet active against AXIOM: the AXIOM routes it uses (lock, human re-arm, event feed) are not in the build of AXIOM in this repository. The reference guard is not fail-closed: if the guard process itself stops, nothing here locks.

An operator can plug in a stricter guard. See `docs/REFERENCE_GUARD.md`.
