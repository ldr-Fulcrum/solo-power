"""Canonical serialization and hashing (see docs/EFFECT_JOURNAL_SPEC.md section 3)."""
import hashlib
import json

GENESIS = "0" * 64
ENTRY_VERSION = 1


def canonical(obj) -> bytes:
    """Sorted keys, no whitespace, UTF-8, no ASCII escaping. Journal entries hold only str, int and null."""
    _reject_floats(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _reject_floats(obj):
    if isinstance(obj, float):
        raise TypeError("floats are not allowed in canonical entries")
    if isinstance(obj, dict):
        for v in obj.values():
            _reject_floats(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _reject_floats(v)


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def entry_hash(entry: dict) -> str:
    """Hash of the entry without its own entry_hash field."""
    return sha256_hex(canonical({k: v for k, v in entry.items() if k != "entry_hash"}))


def value_hash(value) -> str:
    """Hash of tool arguments or results. Values are never stored, only this digest.

    Rule: JSON with sorted keys and compact separators; anything that is not JSON is stringified.
    This rule is local to the Python adapter: the verifier never recomputes it.
    """
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return sha256_hex(text.encode("utf-8"))
