"""Append-only, hash-chained effect journal writer (see docs/EFFECT_JOURNAL_SPEC.md)."""
import contextlib
import datetime
import json
import os

from .canonical import ENTRY_VERSION, GENESIS, entry_hash, value_hash

try:  # POSIX
    import fcntl

    def _lock_fd(fd):
        fcntl.flock(fd, fcntl.LOCK_EX)

    def _unlock_fd(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)
except ImportError:  # Windows (tests on a developer machine)
    import msvcrt

    def _lock_fd(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

    def _unlock_fd(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


class JournalCorrupt(Exception):
    """The journal file cannot be extended safely (torn or unparseable tail)."""


def utc_now() -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (now.microsecond // 1000)


@contextlib.contextmanager
def _file_lock(path):
    fd = os.open(path + ".lock", os.O_CREAT | os.O_RDWR)
    try:
        _lock_fd(fd)
        yield
    finally:
        try:
            _unlock_fd(fd)
        finally:
            os.close(fd)


def read_entries(path):
    """Return the parsed entries of a journal file. Raises JournalCorrupt on an unparseable line."""
    entries = []
    if not os.path.exists(path):
        return entries
    with open(path, "r", encoding="utf-8", newline="") as f:
        for n, line in enumerate(f, start=1):
            if not line.strip():
                continue
            if not line.endswith("\n"):
                raise JournalCorrupt("line %d is not newline-terminated (torn write)" % n)
            try:
                entries.append(json.loads(line))
            except ValueError as exc:
                raise JournalCorrupt("line %d is not valid JSON" % n) from exc
    return entries


class Journal:
    """Writer. Each call opens, locks, appends one line, flushes and fsyncs, so concurrent hook processes
    keep one consistent chain."""

    def __init__(self, path, journal_id=None, clock=utc_now):
        self.path = path
        self.clock = clock
        existing = read_entries(path)
        if existing:
            self.journal_id = existing[0]["journal_id"]
            if journal_id and journal_id != self.journal_id:
                raise JournalCorrupt("journal_id does not match the existing file")
        else:
            if not journal_id:
                raise ValueError("journal_id is required for a new journal")
            self.journal_id = journal_id

    # -- public calls -------------------------------------------------------------------------------
    def reserve(self, tool, args, call_id):
        """Record that a call is about to run. Returns the entry. Arguments are hashed, never stored."""
        return self._append(kind="RESERVE", tool=tool, call_id=call_id, args_hash=value_hash(args),
                            ref=None, outcome=None, result_hash=None)

    def seal(self, tool, call_id, outcome, result=None):
        """Record how a call ended. Links to the open reservation for call_id when there is one."""
        if outcome not in ("OK", "ERROR", "BLOCKED"):
            raise ValueError("outcome must be OK, ERROR or BLOCKED")
        return self._append(kind="SEAL", tool=tool, call_id=call_id, args_hash=None, ref="OPEN",
                            outcome=outcome, result_hash=value_hash(result) if result is not None else None)

    def head(self):
        """(seq, entry_hash) of the last entry, or (0, GENESIS)."""
        entries = read_entries(self.path)
        return (entries[-1]["seq"], entries[-1]["entry_hash"]) if entries else (0, GENESIS)

    # -- internals ------------------------------------------------------------------------------------
    def _append(self, **fields):
        with _file_lock(self.path):
            entries = read_entries(self.path)
            seq = len(entries) + 1
            prev = entries[-1]["entry_hash"] if entries else GENESIS
            if entries and entries[0]["journal_id"] != self.journal_id:
                raise JournalCorrupt("journal_id does not match the file")
            if entries and entries[-1]["seq"] != len(entries):
                raise JournalCorrupt("existing journal has a sequence gap; refusing to extend it")
            if fields["ref"] == "OPEN":
                fields["ref"] = _open_reservation(entries, fields["call_id"])
            entry = {"v": ENTRY_VERSION, "journal_id": self.journal_id, "seq": seq, "ts": self.clock(),
                     "prev_hash": prev, **fields}
            entry["entry_hash"] = entry_hash(entry)
            line = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n"
            with open(self.path, "ab") as f:
                f.write(line.encode("utf-8"))
                f.flush()
                os.fsync(f.fileno())
            return entry


def _open_reservation(entries, call_id):
    """seq of the latest RESERVE for call_id that has no SEAL yet, else None."""
    sealed = {e["ref"] for e in entries if e["kind"] == "SEAL" and e["ref"] is not None}
    for e in reversed(entries):
        if e["kind"] == "RESERVE" and e["call_id"] == call_id and e["seq"] not in sealed:
            return e["seq"]
    return None
