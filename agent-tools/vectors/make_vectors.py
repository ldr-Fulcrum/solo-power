"""Produce vectors/effect_journal_vectors.json.

This script restates the hash rule from docs/EFFECT_JOURNAL_SPEC.md in a few lines of plain hashlib so the
vectors do not come from the core under test. The expected verifier results are written by hand below,
not computed. Run: python vectors/make_vectors.py
"""
import copy
import hashlib
import json
import os

ZERO = "0" * 64
JID = "j-test-0001"


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sha(b):
    return hashlib.sha256(b).hexdigest()


def entry(seq, ts, kind, tool, call_id, args_hash, ref, outcome, result_hash, prev):
    e = {"v": 1, "journal_id": JID, "seq": seq, "ts": ts, "kind": kind, "tool": tool, "call_id": call_id,
         "args_hash": args_hash, "ref": ref, "outcome": outcome, "result_hash": result_hash, "prev_hash": prev}
    e["entry_hash"] = sha(canon({k: v for k, v in e.items() if k != "entry_hash"}))
    return e


def chain(specs):
    out, prev = [], ZERO
    for i, s in enumerate(specs, start=1):
        e = entry(i, *s, prev)
        out.append(e)
        prev = e["entry_hash"]
    return out


AH1, AH2 = sha(b"args-1"), sha(b"args-2")
RH1, RH2 = sha(b"result-1"), sha(b"result-2")
SPECS = [
    ("2026-10-28T10:00:00.000Z", "RESERVE", "task.create", "c1", AH1, None, None, None),
    ("2026-10-28T10:00:01.000Z", "SEAL", "task.create", "c1", None, 1, "OK", RH1),
    ("2026-10-28T10:00:02.000Z", "RESERVE", "plan.create", "c2", AH2, None, None, None),
    ("2026-10-28T10:00:03.000Z", "SEAL", "plan.create", "c2", None, 3, "ERROR", RH2),
]
GOOD = chain(SPECS)
NOW = "2026-10-28T10:10:00.000Z"


def anchor(e):
    return {"journal_id": JID, "seq": e["seq"], "head_hash": e["entry_hash"]}


def case(name, entries, anchors, expected, now=NOW, grace=300):
    return {"name": name, "entries": entries, "anchors": anchors, "now": now, "grace_seconds": grace,
            "expected": {"ok": not any(sev == "ERROR" for _, _, sev in expected),
                         "findings": [[t, s, sev] for t, s, sev in sorted(expected, key=lambda x: (x[1], x[0]))]}}


def cases():
    out = []
    out.append(case("clean-anchored-at-head", GOOD, [anchor(GOOD[3])], []))
    # Edit a field without rehashing.
    c = copy.deepcopy(GOOD)
    c[1]["tool"] = "task.delete"
    out.append(case("edit-field-no-rehash", c, [anchor(GOOD[3])], [("BAD_HASH", 2, "ERROR"), ("BAD_LINK", 3, "ERROR")]))
    # Edit entry 2 and rehash that entry only: the next entry's link breaks.
    c = copy.deepcopy(GOOD)
    c[1]["tool"] = "task.delete"
    c[1]["entry_hash"] = sha(canon({k: v for k, v in c[1].items() if k != "entry_hash"}))
    out.append(case("edit-field-rehash-entry-only", c, [anchor(GOOD[3])], [("BAD_LINK", 3, "ERROR")]))
    # Rebuild the whole chain consistently after an edit: chain is clean, only the anchor catches it.
    spec2 = [list(s) for s in SPECS]
    spec2[0][2] = "task.delete"
    rebuilt = chain([tuple(s) for s in spec2])
    out.append(case("rebuilt-chain-caught-by-anchor", rebuilt, [anchor(GOOD[3])], [("TAMPER", 4, "ERROR")]))
    # Delete entry 2.
    c = [copy.deepcopy(GOOD[0]), copy.deepcopy(GOOD[2]), copy.deepcopy(GOOD[3])]
    out.append(case("delete-middle-entry", c, [anchor(GOOD[3])],
                    [("SEQ_GAP", 3, "ERROR"), ("BAD_LINK", 3, "ERROR"), ("UNSEALED", 1, "WARN")]))
    # Truncate the tail: only an anchor can tell.
    out.append(case("truncate-tail-caught-by-anchor", GOOD[:2], [anchor(GOOD[3])], [("TRUNCATION", 4, "ERROR")]))
    out.append(case("truncate-tail-no-anchor-undetectable", GOOD[:2], [], []))
    # Reorder entries 2 and 3.
    c = [copy.deepcopy(GOOD[0]), copy.deepcopy(GOOD[2]), copy.deepcopy(GOOD[1]), copy.deepcopy(GOOD[3])]
    out.append(case("reorder-two-entries", c, [anchor(GOOD[3])],
                    [("SEQ_GAP", 2, "ERROR"), ("BAD_LINK", 2, "ERROR"), ("SEQ_GAP", 3, "ERROR"), ("BAD_LINK", 3, "ERROR"),
                     ("SEQ_GAP", 4, "ERROR"), ("BAD_LINK", 4, "ERROR")]))
    # Unsealed reservation: old enough to report.
    specs = SPECS[:3]
    c = chain(specs)
    out.append(case("unsealed-after-grace", c, [anchor(c[2])], [("UNSEALED", 3, "WARN")]))
    out.append(case("unsealed-within-grace", c, [anchor(c[2])], [], now="2026-10-28T10:03:00.000Z"))
    # Seal without a reservation.
    c = chain([("2026-10-28T10:00:00.000Z", "SEAL", "task.create", "c9", None, None, "OK", RH1)])
    out.append(case("orphan-seal", c, [], [("ORPHAN_SEAL", 1, "WARN")]))
    # Duplicate seal.
    c = chain(SPECS[:2] + [("2026-10-28T10:00:02.000Z", "SEAL", "task.create", "c1", None, 1, "OK", RH1)])
    out.append(case("duplicate-seal", c, [], [("BAD_SEAL", 3, "ERROR")]))
    # Seal pointing at a missing or wrong reservation.
    c = chain(SPECS[:1] + [("2026-10-28T10:00:01.000Z", "SEAL", "task.create", "c1", None, 7, "OK", RH1)])
    out.append(case("seal-bad-ref", c, [], [("BAD_SEAL", 2, "ERROR"), ("UNSEALED", 1, "WARN")]))
    # Two anchors disagree.
    bad = {"journal_id": JID, "seq": 2, "head_hash": sha(b"x")}
    out.append(case("anchor-mismatch-mid-chain", GOOD, [bad, anchor(GOOD[3])], [("TAMPER", 2, "ERROR")]))
    return out


def main():
    hash_vectors = []
    for obj in [{"b": 1, "a": "x"}, {"k": None, "z": [1, 2, {"y": "é"}]}, {"s": "line\nbreak \"q\""}]:
        hash_vectors.append({"input": obj, "canonical": canon(obj).decode("utf-8"), "sha256": sha(canon(obj))})
    doc = {
        "spec": "effect-journal/1",
        "genesis_prev_hash": ZERO,
        "hash_vectors": hash_vectors,
        "chain_vector": {"journal_id": JID, "entries": GOOD},
        "verify_cases": cases(),
    }
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "effect_journal_vectors.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, indent=1, sort_keys=True, ensure_ascii=False)
        f.write("\n")
    print("wrote", len(doc["verify_cases"]), "verify cases")


if __name__ == "__main__":
    main()
