"""Anchoring: store the journal's head hash somewhere the agent cannot edit (spec section 5).

Two implementations of one small interface:
  FileAnchorStore   an append-only file on the host, outside the sandbox (offline use, tests, verifier input)
  AxiomAnchorClient posts to an AXIOM build that provides POST /api/v1/runtime/journal-anchors
                    (not yet active against AXIOM: the AXIOM build in this repository lacks that route)
Both take {journal_id, seq, head_hash}. The bearer token is read from an environment variable at call time
and is never written anywhere.
"""
import json
import os
import urllib.error
import urllib.request


class AnchorError(Exception):
    """The anchor could not be stored. In GATE mode the tool call is blocked."""


class FileAnchorStore:
    def __init__(self, path):
        self.path = path

    def post(self, journal_id, seq, head_hash):
        existing = self.read(journal_id)
        top = max((a["seq"] for a in existing), default=0)
        if seq < top:
            raise AnchorError("seq %d is lower than the stored maximum %d" % (seq, top))
        rec = {"journal_id": journal_id, "seq": seq, "head_hash": head_hash}
        with open(self.path, "ab") as f:
            f.write((json.dumps(rec, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        return "file:%s:%d" % (journal_id, seq)

    def read(self, journal_id=None):
        if not os.path.exists(self.path):
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    rec = json.loads(line)
                    if journal_id is None or rec["journal_id"] == journal_id:
                        out.append(rec)
        return out


class AxiomAnchorClient:
    PATH = "/api/v1/runtime/journal-anchors"

    def __init__(self, base_url, token_env, timeout=5.0, opener=None):
        self.url = base_url.rstrip("/") + self.PATH
        self.token_env = token_env
        self.timeout = timeout
        self._open = opener or urllib.request.urlopen

    def post(self, journal_id, seq, head_hash):
        token = os.environ.get(self.token_env, "")
        if not token:
            raise AnchorError("token environment variable %s is not set" % self.token_env)
        body = json.dumps({"journal_id": journal_id, "seq": seq, "head_hash": head_hash}).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + token})
        try:
            with self._open(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise AnchorError("anchor request failed: %s" % type(exc).__name__) from exc
        return str(data.get("id") or data.get("anchor_id") or "")
