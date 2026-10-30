import json
import os
import tempfile
import threading
import unittest

from effect_journal import GENESIS, Journal, JournalCorrupt, canonical, entry_hash, read_entries, sha256_hex, verify
from effect_journal.anchor import AnchorError, AxiomAnchorClient, FileAnchorStore
from effect_journal import hook

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "..", "vectors", "effect_journal_vectors.json"), encoding="utf-8") as _f:
    VECTORS = json.load(_f)


class VectorTests(unittest.TestCase):
    def test_genesis(self):
        self.assertEqual(VECTORS["genesis_prev_hash"], GENESIS)

    def test_canonical_hash_vectors(self):
        for v in VECTORS["hash_vectors"]:
            self.assertEqual(canonical(v["input"]).decode("utf-8"), v["canonical"])
            self.assertEqual(sha256_hex(canonical(v["input"])), v["sha256"])

    def test_chain_vector_entry_hashes(self):
        prev = GENESIS
        for e in VECTORS["chain_vector"]["entries"]:
            self.assertEqual(e["prev_hash"], prev)
            self.assertEqual(entry_hash(e), e["entry_hash"])
            prev = e["entry_hash"]

    def test_verify_cases(self):
        for case in VECTORS["verify_cases"]:
            with self.subTest(case["name"]):
                got = verify(case["entries"], case["anchors"], now=case["now"], grace_seconds=case["grace_seconds"])
                self.assertEqual(got["ok"], case["expected"]["ok"])
                self.assertEqual(sorted(map(tuple, got["findings"]), key=lambda f: (f[1], f[0])),
                                 sorted(map(tuple, case["expected"]["findings"]), key=lambda f: (f[1], f[0])))

    def test_floats_rejected_in_entries(self):
        with self.assertRaises(TypeError):
            canonical({"a": 1.5})


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "j.jsonl")
        self.j = Journal(self.path, "j-unit-1")

    def tearDown(self):
        self.dir.cleanup()

    def _run_two_calls(self):
        self.j.reserve("task.create", {"title": "x"}, "c1")
        self.j.seal("task.create", "c1", "OK", {"id": 1})
        self.j.reserve("plan.create", {"p": 1}, "c2")
        self.j.seal("plan.create", "c2", "ERROR", {"e": "boom"})

    def test_write_then_verify_clean(self):
        self._run_two_calls()
        entries = read_entries(self.path)
        self.assertEqual([e["seq"] for e in entries], [1, 2, 3, 4])
        self.assertEqual(entries[1]["ref"], 1)
        self.assertEqual(entries[3]["ref"], 3)
        seq, head = self.j.head()
        res = verify(entries, [{"journal_id": "j-unit-1", "seq": seq, "head_hash": head}], now="2999-01-01T00:00:00.000Z")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["unanchored_tail"], 0)

    def test_edit_delete_truncate_reorder_are_flagged(self):
        self._run_two_calls()
        entries = read_entries(self.path)
        seq, head = self.j.head()
        anchors = [{"journal_id": "j-unit-1", "seq": seq, "head_hash": head}]
        edited = json.loads(json.dumps(entries)); edited[1]["outcome"] = "ERROR"
        self.assertFalse(verify(edited, anchors)["ok"])
        deleted = entries[:1] + entries[2:]
        self.assertFalse(verify(deleted, anchors)["ok"])
        self.assertIn("TRUNCATION", [f[0] for f in verify(entries[:2], anchors)["findings"]])
        reordered = [entries[0], entries[2], entries[1], entries[3]]
        self.assertFalse(verify(reordered, anchors)["ok"])

    def test_crash_between_reserve_and_seal_is_unsealed_and_not_resent(self):
        self.j.reserve("task.create", {"title": "x"}, "c1")  # process "dies" here
        reopened = Journal(self.path)
        entries = read_entries(self.path)
        self.assertEqual(len(entries), 1)  # reopening writes nothing, resends nothing
        res = verify(entries, [], now="2999-01-01T00:00:00.000Z")
        self.assertEqual([f[0] for f in res["findings"]], ["UNSEALED"])
        self.assertTrue(res["ok"])
        self.assertEqual(reopened.journal_id, "j-unit-1")

    def test_canary_never_reaches_the_file(self):
        canary = "CANARY-9f3a-secret-value"
        self.j.reserve("web.fetch", {"url": "https://example.test/", "auth": canary}, "c1")
        self.j.seal("web.fetch", "c1", "OK", {"body": canary})
        with open(self.path, "rb") as f:
            self.assertNotIn(canary.encode(), f.read())

    def test_concurrent_writers_keep_one_chain(self):
        def work(n):
            j = Journal(self.path, "j-unit-1")
            for i in range(10):
                j.reserve("t", {"n": n, "i": i}, "c-%d-%d" % (n, i))
                j.seal("t", "c-%d-%d" % (n, i), "OK")
        threads = [threading.Thread(target=work, args=(n,)) for n in range(4)]
        [t.start() for t in threads]; [t.join() for t in threads]
        entries = read_entries(self.path)
        self.assertEqual(len(entries), 80)
        res = verify(entries, [], now="2999-01-01T00:00:00.000Z")
        self.assertTrue(res["ok"], res["findings"][:5])
        self.assertEqual(res["findings"], [])

    def test_torn_tail_refuses_extension(self):
        self._run_two_calls()
        with open(self.path, "ab") as f:
            f.write(b'{"v":1,"seq":5')  # torn write, no newline
        with self.assertRaises(JournalCorrupt):
            Journal(self.path).reserve("t", {}, "c9")

    def test_seal_without_reservation_is_an_orphan_warning(self):
        self.j.seal("task.create", "never-reserved", "OK")
        res = verify(read_entries(self.path))
        self.assertEqual(res["findings"], [["ORPHAN_SEAL", 1, "WARN"]])


class AnchorTests(unittest.TestCase):
    def test_file_store_is_append_only_and_rejects_lower_seq(self):
        with tempfile.TemporaryDirectory() as d:
            s = FileAnchorStore(os.path.join(d, "a.jsonl"))
            s.post("j", 5, "h5")
            with self.assertRaises(AnchorError):
                s.post("j", 4, "h4")
            s.post("j", 5, "h5b")  # same seq again is accepted (the verifier will see the conflict)
            self.assertEqual(len(s.read("j")), 2)

    def test_axiom_client_needs_token_and_reports_failure_without_secrets(self):
        os.environ.pop("EJ_TEST_TOKEN", None)
        c = AxiomAnchorClient("http://127.0.0.1:1", "EJ_TEST_TOKEN")
        with self.assertRaises(AnchorError) as ctx:
            c.post("j", 1, "h")
        self.assertNotIn("Bearer", str(ctx.exception))
        os.environ["EJ_TEST_TOKEN"] = "tok-123456"
        try:
            with self.assertRaises(AnchorError) as ctx2:
                c.post("j", 1, "h")  # nothing listens on port 1
            self.assertNotIn("tok-123456", str(ctx2.exception))
        finally:
            os.environ.pop("EJ_TEST_TOKEN", None)

    def test_axiom_client_sends_expected_request(self):
        seen = {}

        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"id": "anc-1"}'

        def opener(req, timeout):
            seen["url"], seen["body"], seen["auth"] = req.full_url, json.loads(req.data), req.get_header("Authorization")
            return Resp()

        os.environ["EJ_TEST_TOKEN"] = "tok-abc"
        try:
            got = AxiomAnchorClient("http://axiom.test/", "EJ_TEST_TOKEN", opener=opener).post("j", 3, "hh")
        finally:
            os.environ.pop("EJ_TEST_TOKEN", None)
        self.assertEqual(got, "anc-1")
        self.assertEqual(seen["url"], "http://axiom.test/api/v1/runtime/journal-anchors")
        self.assertEqual(seen["body"], {"journal_id": "j", "seq": 3, "head_hash": "hh"})
        self.assertEqual(seen["auth"], "Bearer tok-abc")


class FailingAnchor:
    def post(self, *a):
        raise AnchorError("down")


class HookTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "j.jsonl")
        self.env = {"EJ_JOURNAL": self.path, "EJ_JOURNAL_ID": "j-hook-1"}

    def tearDown(self):
        self.dir.cleanup()

    def _pre(self, cid="t1", args=None):
        return {"hook_event_name": "pre_tool_call", "tool_name": "task.create", "tool_input": args or {"a": 1},
                "session_id": "s", "extra": {"tool_call_id": cid}}

    def _post(self, cid="t1", status="ok"):
        return {"hook_event_name": "post_tool_call", "tool_name": "task.create", "tool_input": {"a": 1},
                "session_id": "s", "extra": {"tool_call_id": cid, "status": status, "result": {"r": 1}}}

    def test_pre_and_post_pair_up(self):
        with tempfile.TemporaryDirectory() as d:
            store = FileAnchorStore(os.path.join(d, "a.jsonl"))
            self.assertEqual(hook.handle(self._pre(), self.env, store)[0], 0)
            self.assertEqual(hook.handle(self._post(), self.env, store)[0], 0)
            entries = read_entries(self.path)
            self.assertEqual([e["kind"] for e in entries], ["RESERVE", "SEAL"])
            self.assertEqual(entries[1]["ref"], 1)
            res = verify(entries, store.read("j-hook-1"), now="2999-01-01T00:00:00.000Z")
            self.assertEqual(res["findings"], [])
            self.assertEqual(res["unanchored_tail"], 0)

    def test_gate_mode_blocks_when_anchor_fails(self):
        env = dict(self.env, EJ_MODE="gate")
        code, out = hook.handle(self._pre(), env, FailingAnchor())
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["decision"], "block")

    def test_gate_mode_blocks_without_any_anchor_target(self):
        code, out = hook.handle(self._pre(), dict(self.env, EJ_MODE="gate"), None)
        self.assertEqual(code, 2)

    def test_observe_mode_proceeds_when_anchor_fails(self):
        self.assertEqual(hook.handle(self._pre(), self.env, FailingAnchor())[0], 0)
        self.assertEqual(len(read_entries(self.path)), 1)

    def test_gate_mode_blocks_when_journal_is_unwritable(self):
        env = dict(self.env, EJ_MODE="gate", EJ_JOURNAL=os.path.join(self.dir.name, "no-such-dir", "j.jsonl"))
        code, out = hook.handle(self._pre(), env, FailingAnchor())
        self.assertEqual(code, 2)

    def test_error_status_seals_as_error_and_missing_call_id_is_derived(self):
        pre = self._pre(); pre["extra"] = {}
        post = self._post(status="error"); post["extra"].pop("tool_call_id")
        hook.handle(pre, self.env, None)
        hook.handle(post, self.env, None)
        entries = read_entries(self.path)
        self.assertEqual(entries[1]["outcome"], "ERROR")
        self.assertTrue(entries[0]["call_id"].startswith("derived-"))

    def test_hook_never_writes_argument_or_result_text(self):
        canary = "CANARY-hook-77c1"
        hook.handle(self._pre(args={"secret": canary}), self.env, None)
        post = self._post(); post["extra"]["result"] = {"secret": canary}
        hook.handle(post, self.env, None)
        with open(self.path, "rb") as f:
            self.assertNotIn(canary.encode(), f.read())


if __name__ == "__main__":
    unittest.main()
