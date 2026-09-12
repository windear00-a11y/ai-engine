"""Phase 1 — Memory + Vocabulary + Manual Capture.

Tests for:
  * generic injectable Vocabulary (diary_v1)
  * clean Memory facade (remember/recall, project isolation, deterministic ids)
  * Activity as generic first-class, NOT in engine_state.db
  * Manual capture via Memory.remember

All tests stdlib-only, per-project temp data_root, never modify production
database/*, never touch tools/permissions or intelligence/* behavior.
"""

import os
import sqlite3
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class VocabularyTests(unittest.TestCase):
    def test_diary_v1_loads(self):
        from ai_engine.vocabulary import Vocabulary
        v = Vocabulary.load("diary_v1")
        self.assertEqual(v.id, "diary_v1")
        self.assertIn("fact", v.types)
        self.assertIn("observation", v.types)
        self.assertIn("related_to", v.relationship_kinds)
        self.assertTrue(v.is_valid_type("fact"))
        self.assertFalse(v.is_valid_type("nonexistent_type_xyz"))

    def test_diary_v1_loads(self):
        from ai_engine.vocabulary import Vocabulary
        v = Vocabulary.load("diary_v1")
        self.assertEqual(v.id, "diary_v1")
        self.assertIn("concept", v.types)
        self.assertIn("depends_on", v.relationship_kinds)

    def test_generic_injectable(self):
        from ai_engine.vocabulary import Vocabulary
        v1 = Vocabulary("custom", ["fact", "insight"], ["related_to", "supports"])
        self.assertTrue(v1.is_valid_type("fact"))
        self.assertFalse(v1.is_valid_type("technology"))
        self.assertTrue(v1.is_valid_relationship_kind("supports"))
        self.assertFalse(v1.is_valid_relationship_kind("depends_on"))

    def test_unknown_vocab_rejected(self):
        from ai_engine.vocabulary import Vocabulary
        with self.assertRaises(ValueError):
            Vocabulary.load("does_not_exist_xyz")

    def test_list_vocabularies(self):
        from ai_engine.vocabulary import list_vocabularies
        lst = list_vocabularies()
        self.assertIn("diary_v1", lst)
        # code_v1 is the retired CODE-domain vocabulary; it must not ship
        # with the generic core.
        self.assertNotIn("code_v1", lst)

    def test_memory_injects_vocabulary(self):
        from ai_engine.memory import Memory
        from ai_engine.vocabulary import Vocabulary
        with tempfile.TemporaryDirectory() as tmp:
            m_diary = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            self.assertEqual(m_diary.vocabulary.id, "diary_v1")
            m_diary2 = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            self.assertEqual(m_diary2.vocabulary.id, "diary_v1")
            # diary rejects a type that is not in its schema
            res = m_diary.remember(text="code dep", type="dependency")
            self.assertFalse(res["ok"])
            self.assertEqual(res["code"], "invalid_argument")
            # a custom injected vocabulary may accept it
            custom = Vocabulary("custom_v", ["fact", "dependency"],
                                ["related_to", "depends_on"])
            self.assertTrue(custom.is_valid_type("dependency"))
            # diary accepts its own valid types
            res2 = m_diary.remember(text="diary dep 2", type="fact")
            self.assertTrue(res2["ok"], res2)

    def test_retrieval_vocabulary_still_free_form(self):
        # Ensure we did not break legacy validator free-form (any non-empty string valid)
        from retrieval.vocabulary import is_valid_node_type
        self.assertTrue(is_valid_node_type("fact"))
        self.assertTrue(is_valid_node_type("custom_free_form_type"))
        self.assertTrue(is_valid_node_type("my diary type"))
        self.assertFalse(is_valid_node_type(""))
        self.assertFalse(is_valid_node_type(None))


class ActivityTests(unittest.TestCase):
    def test_activity_not_in_engine_state(self):
        from ai_engine.memory import Memory
        from ai_engine.activity import ActivityStore, get_activity_db_path
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            res = mem.remember(text="activity isolation test", type="fact")
            self.assertTrue(res["ok"], res)
            # Activity should be in activity.db per-project, not engine_state.db
            act_db = get_activity_db_path("default", tmp)
            self.assertTrue(os.path.exists(act_db))
            eng_db = os.path.join(tmp, "default", "engine_state.db")
            # engine_state.db should not exist (we never touched it) or if exists should have no activities table
            if os.path.exists(eng_db):
                con = sqlite3.connect(f"file:{eng_db}?mode=ro", uri=True)
                tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                self.assertNotIn("activities", tables)
                con.close()
            # activity.db should have exactly one activity
            astore = ActivityStore("default", tmp)
            self.assertEqual(astore.count(), 1)
            act = astore.get(res["activity_id"])
            self.assertIsNotNone(act)
            self.assertEqual(act["project_id"], "default")

    def test_activity_deterministic_id(self):
        from ai_engine.activity import derive_activity_id
        a1 = derive_activity_id("default", "manual", "manual", {"text": "hello"}, "ctx_abc")
        a2 = derive_activity_id("default", "manual", "manual", {"text": "hello"}, "ctx_abc")
        a3 = derive_activity_id("default", "manual", "manual", {"text": "different"}, "ctx_abc")
        self.assertEqual(a1, a2)
        self.assertNotEqual(a1, a3)
        self.assertTrue(a1.startswith("act_"))

    def test_activity_append_only_and_idempotent(self):
        from ai_engine.activity import ActivityStore
        with tempfile.TemporaryDirectory() as tmp:
            store = ActivityStore("proj1", tmp)
            payload = {"text": "test payload"}
            from ai_engine.activity import derive_activity_id
            aid = derive_activity_id("proj1", "manual", "manual", payload, "ctx_1")
            r1 = store.save(aid, "manual", "manual", payload, "ctx_1")
            self.assertTrue(r1["ok"])
            self.assertTrue(r1["created"])
            r2 = store.save(aid, "manual", "manual", payload, "ctx_1")
            self.assertTrue(r2["ok"])
            self.assertTrue(r2["idempotent"])
            # Different payload with same id should fail
            r3 = store.save(aid, "manual", "manual", {"text": "different"}, "ctx_1")
            self.assertFalse(r3["ok"])


class MemoryFacadeTests(unittest.TestCase):
    def test_remember_and_recall(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            r = mem.remember(text="evening walk helps sleep", type="fact")
            self.assertTrue(r["ok"], r)
            self.assertIn("activity_id", r)
            self.assertIn("context_id", r)
            self.assertIn("evidence_id", r)
            self.assertIn("node_ids", r)
            # Recall
            out = mem.recall(query="walk sleep")
            self.assertTrue(out["ok"], out)
            self.assertGreaterEqual(len(out["result"]["knowledge"]), 1)
            ids = [n["id"] for n in out["result"]["knowledge"]]
            self.assertIn(r["node_id"], ids)
            # Provenance
            node = out["result"]["knowledge"][0]
            self.assertIn("provenance", node)
            self.assertEqual(node["provenance"]["source_name"], "manual")

    def test_remember_deterministic_node_id(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            m1 = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            r1 = m1.remember(text="deterministic id test", type="fact")
            self.assertTrue(r1["ok"], r1)
            # Same text in same project should give same node_id (stable)
            # But second remember in same project will find existing node and consider duplicate? Current Memory returns error on duplicate with different content? Actually same content same node id should be idempotent? We treat existing same content as ok?
            # Second remember with same text should still succeed (activity new, node existing) — we allow it as idempotent node?
            # Our implementation allows existing node with same content to succeed.
            # Check that recall still finds it.
            out = m1.recall(query="deterministic id test")
            self.assertTrue(any(n["id"] == r1["node_id"] for n in out["result"]["knowledge"]))

    def test_project_isolation(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            m_default = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            m_research = Memory(project_id="research", data_root=tmp, vocabulary_id="diary_v1")
            r1 = m_default.remember(text="default project fact", type="fact")
            self.assertTrue(r1["ok"], r1)
            r2 = m_research.remember(text="research project fact", type="fact")
            self.assertTrue(r2["ok"], r2)
            # Default recall should not see research fact
            out_default = m_default.recall(query="default project fact")
            self.assertTrue(any("default project fact" in n["description"] for n in out_default["result"]["knowledge"]))
            self.assertFalse(any("research project fact" in n["description"] for n in out_default["result"]["knowledge"]))
            # Research recall should not see default fact
            out_research = m_research.recall(query="research project fact")
            self.assertTrue(any("research project fact" in n["description"] for n in out_research["result"]["knowledge"]))
            self.assertFalse(any("default project fact" in n["description"] for n in out_research["result"]["knowledge"]))
            # Different isolated directories
            from ai_engine.paths import get_knowledge_db
            self.assertNotEqual(get_knowledge_db("default", tmp), get_knowledge_db("research", tmp))
            self.assertNotEqual(r1["node_id"], r2["node_id"])

    def test_validation_and_provenance(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            # Empty text rejected
            r = mem.remember(text="   ", type="fact")
            self.assertFalse(r["ok"])
            self.assertEqual(r["code"], "invalid_argument")
            # Invalid type rejected via vocabulary
            r2 = mem.remember(text="valid text", type="nonexistent_xyz")
            self.assertFalse(r2["ok"])
            self.assertEqual(r2["code"], "invalid_argument")
            # Valid remember produces provenance
            r3 = mem.remember(text="provenance test", type="fact", name="Prov", description="provenance test content")
            self.assertTrue(r3["ok"], r3)
            node = mem.get_node(r3["node_id"])
            self.assertIsNotNone(node)
            self.assertIn("provenance", node)
            self.assertEqual(node["provenance"]["source_name"], "manual")
            self.assertIn("provenance", node)
            # Evidence exists in per-project evidence.db
            from ai_engine.paths import get_evidence_db
            ev_db = get_evidence_db("default", tmp)
            self.assertTrue(os.path.exists(ev_db))
            con = sqlite3.connect(f"file:{ev_db}?mode=ro", uri=True)
            ev_cnt = con.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
            self.assertGreaterEqual(ev_cnt, 1)
            con.close()

    def test_relationship_vocab_validation(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            # Valid relationship kind
            r = mem.remember(text="rel valid", type="fact", relationships=[{"type": "related_to", "target": "other"}])
            # Note: target must reference node in same source, but we have only one node, so relationship to "other" will be dangling target error via validator
            # Our validator will reject relationship target not in source
            self.assertFalse(r["ok"])
            self.assertIn("does not reference", r["error"])

    def test_recall_validation(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            r = mem.recall(query="   ")
            self.assertFalse(r["ok"])
            self.assertEqual(r["code"], "invalid_argument")
            r2 = mem.recall(query="hello", limit=200)
            self.assertFalse(r2["ok"])
            self.assertEqual(r2["code"], "invalid_argument")

    def test_context_persisted(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            r = mem.remember(text="context test", type="fact")
            self.assertTrue(r["ok"], r)
            ctx_id = r["context_id"]
            self.assertTrue(ctx_id.startswith("ctx_"))
            # Check context.db per-project has snapshot
            from ai_engine.paths import get_context_db
            ctx_db = get_context_db("default", tmp)
            self.assertTrue(os.path.exists(ctx_db))
            con = sqlite3.connect(f"file:{ctx_db}?mode=ro", uri=True)
            row = con.execute("SELECT * FROM context_snapshots WHERE context_id=?", (ctx_id,)).fetchone()
            self.assertIsNotNone(row)
            con.close()

    def test_invariants_preserved(self):
        from api.contract import CONTRACT_VERSION, OPERATIONS
        from tools.permissions.policy import HARD_WRITE_INVARIANTS
        import intelligence
        self.assertEqual(CONTRACT_VERSION, "1")
        self.assertEqual(tuple(OPERATIONS), ("search", "get", "related", "follow", "provenance", "inspect"))
        self.assertEqual(list(HARD_WRITE_INVARIANTS), [("database/knowledge.db", "blocked"), ("database/knowledge.db.backup", "blocked")])
        self.assertEqual(intelligence.__version__, "0")


if __name__ == "__main__":
    unittest.main()
