# Effect journal specification (effect-journal/1)

A write-ahead journal for an AI agent's tool calls. Before a call runs, an entry says it is about to run. After it ends, a second entry says how. Entries are hash-chained, and the chain's head can be stored somewhere the agent cannot edit. This file, with `vectors/effect_journal_vectors.json`, defines correctness. Any implementation in any language must reproduce the vectors. The Python package in `effect_journal/` is one implementation, and its Hermes hook is one adapter.

## 1. What it claims, and what it does not

Claim: each tool call that reaches the hook gets a journal entry before the call is allowed to run. The journal is hash-chained and its head can be anchored outside the agent's reach, so edits, deletions, reordering and (with an anchor) truncation are detectable. A reservation with no seal is reported as UNSEALED. It is never silently retried.

Not claimed: tamper-proofing; protection against someone who controls the machine that holds both the journal and the anchors; coverage of effects that bypass the hook. Whether every tool type in a given agent runtime reaches the hook has to be tested for that runtime.

## 2. Entries

A journal is a UTF-8 file of JSON lines, one entry per line, each ending in `\n`. Every entry has exactly these fields.

| Field | Type | Meaning |
|---|---|---|
| `v` | int | spec version, 1 |
| `journal_id` | string | same for the whole file |
| `seq` | int | 1, 2, 3, ... with no gaps |
| `ts` | string | UTC, `YYYY-MM-DDTHH:MM:SS.mmmZ` |
| `kind` | string | `RESERVE` or `SEAL` |
| `tool` | string | tool name |
| `call_id` | string | the runtime's id for this call; pairs a RESERVE with its SEAL |
| `args_hash` | string or null | SHA-256 hex of the arguments (RESERVE only) |
| `ref` | int or null | on a SEAL, the `seq` of its RESERVE; null otherwise |
| `outcome` | string or null | on a SEAL: `OK`, `ERROR` or `BLOCKED` |
| `result_hash` | string or null | SHA-256 hex of the result (SEAL only), if any |
| `prev_hash` | string | `entry_hash` of the previous entry; 64 zeros for `seq` 1 |
| `entry_hash` | string | see section 3 |

Entries hold identifiers and hashes only. Arguments, results, prompts and secrets are never written.

## 3. Canonical form and hashing

`canonical(x)` is the UTF-8 encoding of JSON with keys sorted, separators `,` and `:` with no spaces, and non-ASCII characters written as themselves. Entries contain only strings, integers and null, so no float formatting is involved.

`entry_hash` = SHA-256 (lowercase hex) of `canonical(entry without the entry_hash field)`.

`args_hash` and `result_hash` are computed by the adapter from the runtime's values. The verifier never recomputes them, so their exact rule is local to each adapter. The Python adapter uses JSON with sorted keys and compact separators, stringifying anything that is not JSON.

## 4. Per-call protocol

1. Before the tool runs: append a RESERVE (fsync).
2. The tool runs.
3. After it ends, in any outcome: append a SEAL with `ref` set to the RESERVE's `seq` (fsync).
4. A crash between 1 and 3 leaves a RESERVE with no SEAL. Nothing is resent. A person decides.

Modes. In `observe` mode anchoring is best effort. In `gate` mode the RESERVE must be anchored before the tool is allowed to run, and if the journal or the anchor fails, the call is blocked.

## 5. Anchors

An anchor is `{journal_id, seq, head_hash}`: the `entry_hash` of the entry at `seq`. It is stored where the agent cannot edit it, append-only. A store rejects an anchor whose `seq` is lower than its stored maximum for the same journal. Two implementations ship: an append-only file on the host (`FileAnchorStore`, works today), and a client for an AXIOM build that provides `POST /api/v1/runtime/journal-anchors` (`AxiomAnchorClient`; not yet active against AXIOM, because the AXIOM build in this repository does not have that route).

## 6. Verification

Input: the entries in file order, the anchors, the current time and a grace period (a default of 300 seconds; it is a parameter, `grace_seconds`, and the vectors state the value they use). Output findings are `[type, seq, severity]`. `ok` is true when no finding has severity `ERROR`.

| Type | Severity | Meaning |
|---|---|---|
| `BAD_HASH` | ERROR | stored `entry_hash` differs from the recomputed one |
| `BAD_LINK` | ERROR | `prev_hash` differs from the previous entry's recomputed hash (genesis for the first) |
| `SEQ_GAP` | ERROR | `seq` is not the previous entry's `seq` plus one (first must be 1) |
| `JOURNAL_ID_MISMATCH` | ERROR | `journal_id` differs from the first entry's |
| `BAD_SEAL` | ERROR | a SEAL's `ref` is not a RESERVE with the same `call_id`, or that RESERVE is already sealed |
| `ORPHAN_SEAL` | WARN | a SEAL with `ref` null: a call was seen ending that was never seen starting |
| `UNSEALED` | WARN | a RESERVE with no SEAL, older than the grace period |
| `TAMPER` | ERROR | the entry at an anchored `seq` has a different hash (or is missing from inside the file) |
| `TRUNCATION` | ERROR | an anchored `seq` is beyond the end of the file |

The result also carries `count`, `head_seq`, `head_hash` and `unanchored_tail` (entries after the highest anchor that verified). Without anchors, removing entries from the end cannot be detected, which is why anchors exist.

## 7. The Hermes hook adapter

Hermes sends one JSON object on stdin for `pre_tool_call` and `post_tool_call` shell hooks: `hook_event_name`, `tool_name`, `tool_input`, `session_id`, `cwd`, `extra` (which carries `tool_call_id`, and for the post event `status` and `result`). To block a call the hook exits 2 and prints `{"decision":"block","reason":"..."}`. Configure the hook with `fail_closed: true` so a hook that errors or times out also blocks. `effect_journal/hook.py` implements this; its docstring lists the environment variables.

## 8. Test vectors

`vectors/effect_journal_vectors.json` holds canonicalization and hash vectors, a four-entry reference chain, and verification cases (clean, field edit, edit with a re-hashed entry, whole chain rebuilt and caught only by the anchor, deleted entry, truncation with and without an anchor, reordering, unsealed inside and outside the grace period, orphan seal, duplicate seal, bad seal reference, anchor mismatch). `vectors/make_vectors.py` regenerates the file with its own short restatement of the hash rule; the expected verifier results in it are written by hand.
