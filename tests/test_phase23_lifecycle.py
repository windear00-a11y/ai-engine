"""Phase 23 regression tests: lifecycle integrity & robustness.

Covers:
    - end-to-end generic lifecycle persistence across all layers (§1)
    - deterministic identity + idempotent dedup (§11)
    - concurrent first-use and writes (single-writer safety, §9)
    - failure/degradation fail-closed behavior (§10)
    - backup/restore rollback integrity (§3)
"""

import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..")))

from ai_engine.lifecycle import run_lifecycle
from ai_engine.memory import Memory
from ai_engine.paths import get_project_dir, get_knowledge_db


class LifecycleE2ETests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="p23_e2e_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_full_lifecycle_persists_all_layers(self):
        res = run_lifecycle(
            situation={"problem": "phase23 lifecycle e2e"},
            project_id="default", data_root=self.root,
            payload={"text": "phase23 lifecycle e2e", "type": "fact"})
        self.assertTrue(res.get("ok"), res)
        for key in ("activity_id", "context_id", "plan", "decision",
                    "effect", "outcome", "experience_id"):
            self.assertIn(key, res)
        proj_dir = get_project_dir("default", self.root)
        for db in ("activity.db", "context.db", "evidence.db",
                   "experience.db", "knowledge.db"):
            self.assertTrue(os.path.exists(os.path.join(proj_dir, db)))
            con = __import__("sqlite3").connect(os.path.join(proj_dir, db))
            try:
                row = con.execute("PRAGMA integrity_check").fetchone()
                self.assertEqual(row[0], "ok")
            finally:
                con.close()

    def test_recall_roundtrip_after_full_lifecycle(self):
        remember_res = Memory(project_id="default", data_root=self.root,
                              vocabulary_id="diary_v1").remember(
            payload={"text": "roundtrip salient phrase", "type": "fact"})
        self.assertTrue(remember_res.get("ok"), remember_res)
        out = Memory(project_id="default", data_root=self.root,
                     vocabulary_id="diary_v1").recall("salient phrase")
        self.assertTrue(out.get("ok"), out)
        hits = (out.get("result") or {}).get("knowledge") or []
        self.assertGreaterEqual(len(hits), 1)


class DeterminismTests(unittest.TestCase):
    def _fresh_root(self):
        root = tempfile.mkdtemp(prefix="p23_det_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            root, ignore_errors=True))
        return root

    def test_ids_stable_across_fresh_roots(self):
        a = Memory(project_id="default", data_root=self._fresh_root(),
                   vocabulary_id="diary_v1").remember(
            payload={"text": "deterministic identity probe", "type": "fact"})
        b = Memory(project_id="default", data_root=self._fresh_root(),
                   vocabulary_id="diary_v1").remember(
            payload={"text": "deterministic identity probe", "type": "fact"})
        self.assertTrue(a.get("ok"), a)
        self.assertTrue(b.get("ok"), b)
        self.assertEqual(a["context_id"], b["context_id"])
        self.assertEqual(a["activity_id"], b["activity_id"])
        self.assertEqual(a["node_id"], b["node_id"])

    def test_duplicate_remember_is_idempotent(self):
        root = self._fresh_root()
        mem = Memory(project_id="default", data_root=root,
                     vocabulary_id="diary_v1")
        a = mem.remember(payload={"text": "idempotent probe", "type": "fact"})
        b = mem.remember(payload={"text": "idempotent probe", "type": "fact"})
        self.assertTrue(a.get("ok"), a)
        self.assertTrue(b.get("ok"), b)
        self.assertEqual(a["node_id"], b["node_id"])
        self.assertEqual(a["activity_id"], b["activity_id"])
        con = __import__("sqlite3").connect(
            get_knowledge_db("default", root))
        try:
            count = con.execute(
                "SELECT COUNT(*) FROM nodes WHERE id = ?",
                (a["node_id"],)).fetchone()[0]
        finally:
            con.close()
        self.assertEqual(count, 1)


class ConcurrencyTests(unittest.TestCase):
    def test_concurrent_first_use_and_remember(self):
        root = tempfile.mkdtemp(prefix="p23_conc_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            root, ignore_errors=True))
        results = {}

        def work(i):
            try:
                mem = Memory(project_id="default", data_root=root,
                             vocabulary_id="diary_v1")
                results[i] = mem.remember(
                    payload={"text": f"concurrent fact {i}", "type": "fact"})
            except Exception as e:  # noqa: BLE001
                results[i] = {"ok": False,
                              "error": type(e).__name__ + ": " + str(e)}

        threads = [threading.Thread(target=work, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 4)
        for i in range(4):
            self.assertTrue(results[i].get("ok"),
                            {i: results[i].get("error")})
        con = __import__("sqlite3").connect(
            get_knowledge_db("default", root))
        try:
            count = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            row = con.execute("PRAGMA integrity_check").fetchone()
        finally:
            con.close()
        self.assertGreaterEqual(count, 4)
        self.assertEqual(row[0], "ok")


class FailureDegradationTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="p23_fail_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_corrupt_knowledge_db_fails_closed(self):
        kb = get_knowledge_db("default", self.root)
        os.makedirs(os.path.dirname(kb), exist_ok=True)
        with open(kb, "wb") as f:
            f.write(b"garbage that is not sqlite " * 200)
        res = Memory(project_id="default", data_root=self.root,
                     vocabulary_id="diary_v1").remember(
            payload={"text": "corruption probe", "type": "fact"})
        self.assertEqual(res.get("ok"), False)
        self.assertEqual(res.get("code"), "internal_error")

    def test_malformed_payload_fails_closed(self):
        mem = Memory(project_id="default", data_root=self.root,
                     vocabulary_id="diary_v1")
        for bad in (5, None, [], {"type": 3}):
            res = mem.remember(payload=bad)
            self.assertIsInstance(res, dict)
            self.assertEqual(res.get("ok"), False, res)
            self.assertNotIn("Traceback", res.get("error") or "")

    def test_invalid_vocab_type_fails_closed(self):
        res = Memory(project_id="default", data_root=self.root,
                     vocabulary_id="diary_v1").remember(
            payload={"text": "bad type probe", "type": "not_a_real_type"})
        self.assertEqual(res.get("ok"), False)
        self.assertEqual(res.get("code"), "invalid_argument")


class RollbackIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="p23_roll_")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_backup_restore_restores_prior_state(self):
        mem = Memory(project_id="default", data_root=self.root,
                     vocabulary_id="diary_v1")
        a = mem.remember(payload={"text": "first kept fact", "type": "fact"})
        self.assertTrue(a.get("ok"), a)
        from ai_engine.persistence import backup_project
        backup = backup_project("default", data_root=self.root)
        self.assertTrue(backup.get("ok"), backup)

        b = mem.remember(payload={"text": "second later fact", "type": "fact"})
        self.assertTrue(b.get("ok"), b)

        backup_file = backup["backup_files"].get("knowledge.db")
        self.assertIsNotNone(backup_file)
        from ai_engine.persistence import restore_project
        restored = restore_project(backup_file, "default",
                                   data_root=self.root)
        self.assertTrue(restored.get("ok"), restored)

        from retrieval.repository import KnowledgeRepository
        repo = KnowledgeRepository(get_knowledge_db("default", self.root))
        repo.initialize()
        try:
            self.assertIsNotNone(repo.get_node(a["node_id"]))
            self.assertIsNone(repo.get_node(b["node_id"]))
        finally:
            repo.close()


if __name__ == "__main__":
    unittest.main()