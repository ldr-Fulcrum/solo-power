"""Verifier (see docs/EFFECT_JOURNAL_SPEC.md section 6). Pure function over entries and anchors."""
import datetime
import json
import sys

from .canonical import GENESIS, entry_hash
from .journal import read_entries, JournalCorrupt


def _parse_ts(ts):
    return datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=datetime.timezone.utc)


def verify(entries, anchors=(), now=None, grace_seconds=300):
    """Return {ok, findings:[[type, seq, severity]], count, head_seq, head_hash, unanchored_tail}.

    ERROR findings make ok false. UNSEALED and ORPHAN_SEAL are WARN: reported, never hidden, never retried.
    """
    findings = []

    def add(kind, seq, sev="ERROR"):
        findings.append([kind, seq, sev])

    journal_id = entries[0]["journal_id"] if entries else None
    recomputed = {}
    by_seq = {}
    prev = None
    for e in entries:
        seq = e.get("seq")
        h = entry_hash(e)
        recomputed[id(e)] = h
        if e.get("journal_id") != journal_id:
            add("JOURNAL_ID_MISMATCH", seq)
        if h != e.get("entry_hash"):
            add("BAD_HASH", seq)
        expected_seq = 1 if prev is None else prev["seq"] + 1
        if seq != expected_seq:
            add("SEQ_GAP", seq)
        expected_prev = GENESIS if prev is None else recomputed[id(prev)]
        if e.get("prev_hash") != expected_prev:
            add("BAD_LINK", seq)
        by_seq.setdefault(seq, e)
        prev = e

    # Reservations and seals.
    sealed_refs = {}
    for e in entries:
        if e.get("kind") != "SEAL":
            continue
        ref = e.get("ref")
        if ref is None:
            add("ORPHAN_SEAL", e["seq"], "WARN")
            continue
        target = by_seq.get(ref)
        if target is None or target.get("kind") != "RESERVE" or target.get("call_id") != e.get("call_id") \
                or ref in sealed_refs:
            add("BAD_SEAL", e["seq"])
            continue
        sealed_refs[ref] = e["seq"]
    if now is not None:
        now_dt = _parse_ts(now)
        for e in entries:
            if e.get("kind") == "RESERVE" and e["seq"] not in sealed_refs:
                age = (now_dt - _parse_ts(e["ts"])).total_seconds()
                if age > grace_seconds:
                    add("UNSEALED", e["seq"], "WARN")

    # Anchors.
    max_seq = max((e["seq"] for e in entries), default=0)
    anchored_ok = 0
    for a in anchors:
        if a.get("journal_id") != journal_id:
            continue
        aseq = a["seq"]
        target = by_seq.get(aseq)
        if target is None:
            add("TRUNCATION" if aseq > max_seq else "TAMPER", aseq)
        elif recomputed[id(target)] != a["head_hash"]:
            add("TAMPER", aseq)
        else:
            anchored_ok = max(anchored_ok, aseq)

    findings.sort(key=lambda f: (f[1] if f[1] is not None else 0, f[0]))
    return {
        "ok": not any(f[2] == "ERROR" for f in findings),
        "findings": findings,
        "count": len(entries),
        "head_seq": max_seq,
        "head_hash": entries[-1]["entry_hash"] if entries else GENESIS,
        "unanchored_tail": max_seq - anchored_ok,
    }


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Verify an effect journal against optional anchors.")
    p.add_argument("journal")
    p.add_argument("--anchors", help="JSON file: a list of {journal_id, seq, head_hash}")
    p.add_argument("--now", help="UTC time (YYYY-MM-DDTHH:MM:SS.mmmZ); default: current time")
    p.add_argument("--grace", type=int, default=300)
    a = p.parse_args(argv)
    try:
        entries = read_entries(a.journal)
    except JournalCorrupt as exc:
        print(json.dumps({"ok": False, "findings": [["BAD_FILE", None, "ERROR"]], "detail": str(exc)}))
        return 1
    anchors = []
    if a.anchors:
        with open(a.anchors, "r", encoding="utf-8") as f:
            anchors = json.load(f)
    now = a.now or datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    result = verify(entries, anchors, now=now, grace_seconds=a.grace)
    print(json.dumps(result, indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
