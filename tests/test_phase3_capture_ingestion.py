"""Phase 3 — Generic Capture / Ingestion Foundation.

Tests for:
  * CaptureAdapter abstraction (ManualCaptureAdapter generic)
  * Raw -> normalization -> vocab validation -> provenance -> dedup -> persistence pipeline
  * Integration with Memory (manual capture via generic foundation)
  * Domain-neutral, per-project isolation, deterministic ids, no engine_state merge

All stdlib-only, per-project temp data_root, never touches legacy DB or API v1.
"""

import os
import sys
import tempfile
import unittest
import sqlite3
import json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class CaptureAdapterTests(unittest.TestCase):
    def test_manual_adapter_normalization_str(self):
        from ai_engine.capture import ManualCaptureAdapter
        a = ManualCaptureAdapter()
        self.assertEqual(a.adapter_id, "manual")
        out = a.normalize("  hello world  ")
        self.assertEqual(out["text"], "hello world")
        self.assertEqual(out["name"], "hello world")
        self.assertEqual(out["description"], "hello world")

    def test_manual_adapter_normalization_dict(self):
        from ai_engine.capture import ManualCaptureAdapter
        a = ManualCaptureAdapter()
        raw = {"text": "my text", "type": "observation", "name": "MyName", "description": "MyDesc", "relationships": []}
        out = a.normalize(raw)
        self.assertEqual(out["text"], "my text")
        self.assertEqual(out["type"], "observation")
        self.assertEqual(out["name"], "MyName")

    def test_manual_adapter_rejects_empty(self):
        from ai_engine.capture import ManualCaptureAdapter
        a = ManualCaptureAdapter()
        with self.assertRaises(ValueError):
            a.normalize("   ")
        with self.assertRaises(ValueError):
            a.normalize({"text": "   "})

    def test_adapter_abstract(self):
        from ai_engine.capture import CaptureAdapter
        with self.assertRaises(TypeError):
            CaptureAdapter()  # cannot instantiate abstract

    def test_generic_no_code_assumption(self):
        from ai_engine.capture import ManualCaptureAdapter
        a = ManualCaptureAdapter()
        # Should handle any vocab type, not just code types
        out = a.normalize({"text": "insight content", "type": "insight"})
        self.assertEqual(out["type"], "insight")
        out2 = a.normalize({"text": "routine content", "type": "routine"})
        self.assertEqual(out2["type"], "routine")


class CapturePipelineTests(unittest.TestCase):
    def test_normalization_vocabulary_validation(self):
        from ai_engine.capture import run_capture
        with tempfile.TemporaryDirectory() as tmp:
            # Valid type
            res = run_capture("valid fact text", project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            self.assertTrue(res["ok"], res)
            # Invalid type should fail closed
            res2 = run_capture({"text": "valid text", "type": "nonexistent_xyz"}, project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            self.assertFalse(res2["ok"])
            self.assertEqual(res2["code"], "invalid_argument")
            self.assertIn("not in vocabulary", res2["error"])

    def test_provenance_deterministic(self):
        from ai_engine.capture import run_capture
        with tempfile.TemporaryDirectory() as tmp:
            r1 = run_capture("provenance test", project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            self.assertTrue(r1["ok"], r1)
            self.assertTrue(r1["activity_id"].startswith("act_"))
            self.assertTrue(r1["context_id"].startswith("ctx_"))
            self.assertTrue(r1["evidence_id"].startswith("ev_"))
            self.assertTrue(r1["node_id"].startswith("fact_"))
            # Provenance includes vocabulary_id, payload_hash
            from ai_engine.paths import get_evidence_db
            ev_db = get_evidence_db("default", tmp)
            con = sqlite3.connect(f"file:{ev_db}?mode=ro", uri=True)
            row = con.execute("SELECT supporting_data_json FROM evidence WHERE evidence_id=?", (r1["evidence_id"],)).fetchone()
            self.assertIsNotNone(row)
            data = json.loads(row[0])
            self.assertEqual(data["vocabulary_id"], "diary_v1")
            self.assertIn("payload_hash", data)
            con.close()

    def test_deterministic_identity_and_dedup(self):
        from ai_engine.capture import run_capture
        with tempfile.TemporaryDirectory() as tmp:
            raw = "dedup test content"
            r1 = run_capture(raw, project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            r2 = run_capture(raw, project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            self.assertTrue(r1["ok"] and r2["ok"])
            # Same project, same raw -> same deterministic ids (dedup)
            self.assertEqual(r1["activity_id"], r2["activity_id"])
            self.assertEqual(r1["context_id"], r2["context_id"])
            self.assertEqual(r1["node_id"], r2["node_id"])
            # Evidence id also deterministic (same activity)
            self.assertEqual(r1["evidence_id"], r2["evidence_id"])
            # Activity count should be 1 (idempotent)
            from ai_engine.activity import ActivityStore
            astore = ActivityStore("default", tmp)
            self.assertEqual(astore.count(), 1)
            # Knowledge node count should be 1
            from ai_engine.paths import get_knowledge_db
            from retrieval.repository import KnowledgeRepository
            repo = KnowledgeRepository(get_knowledge_db("default", tmp))
            repo.initialize()
            self.assertEqual(repo.count_nodes(), 1)
            repo.close()
            # Different project same raw -> different ids (isolation)
            r3 = run_capture(raw, project_id="other", data_root=tmp, vocabulary_id="diary_v1")
            self.assertTrue(r3["ok"], r3)
            self.assertNotEqual(r1["activity_id"], r3["activity_id"])
            # Different project has its own node
            repo2 = KnowledgeRepository(get_knowledge_db("other", tmp))
            repo2.initialize()
            self.assertEqual(repo2.count_nodes(), 1)
            repo2.close()

    def test_relationship_vocab_validation(self):
        from ai_engine.capture import run_capture
        with tempfile.TemporaryDirectory() as tmp:
            # Valid relationship kind for diary_v1
            res = run_capture({"text": "rel test", "relationships": [{"type": "related_to", "target": "fact_dummy"}]}, project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            # Should fail due to target not in same source (dangling), not vocab
            self.assertFalse(res["ok"])
            self.assertIn("does not reference", res["error"])
            # Invalid relationship kind should fail via vocab
            res2 = run_capture({"text": "rel test 2", "relationships": [{"type": "nonexistent_kind_xyz", "target": "t"}]}, project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            self.assertFalse(res2["ok"])
            self.assertIn("not in vocabulary", res2["error"])

    def test_per_project_isolation(self):
        from ai_engine.capture import run_capture
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            r1 = run_capture("project A fact", project_id="proj_a", data_root=tmp, vocabulary_id="diary_v1")
            r2 = run_capture("project B fact", project_id="proj_b", data_root=tmp, vocabulary_id="diary_v1")
            self.assertTrue(r1["ok"] and r2["ok"])
            # Recall via Memory per-project should be isolated
            m_a = Memory(project_id="proj_a", data_root=tmp, vocabulary_id="diary_v1")
            m_b = Memory(project_id="proj_b", data_root=tmp, vocabulary_id="diary_v1")
            out_a = m_a.recall(query="project A fact")
            out_b = m_b.recall(query="project B fact")
            self.assertTrue(any("project A" in n["description"] for n in out_a["result"]["knowledge"]))
            self.assertFalse(any("project B" in n["description"] for n in out_a["result"]["knowledge"]))
            self.assertTrue(any("project B" in n["description"] for n in out_b["result"]["knowledge"]))

    def test_activity_not_in_engine_state(self):
        from ai_engine.capture import run_capture
        with tempfile.TemporaryDirectory() as tmp:
            run_capture("engine state isolation", project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            from ai_engine.paths import get_activity_db
            act_db = get_activity_db("default", tmp)
            self.assertTrue(os.path.exists(act_db))
            eng_db = os.path.join(tmp, "default", "engine_state.db")
            if os.path.exists(eng_db):
                con = sqlite3.connect(f"file:{eng_db}?mode=ro", uri=True)
                tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                self.assertNotIn("activities", tables)
                con.close()

    def test_reuses_ingestion_validator(self):
        # Ensure invalid node (empty name will be truncated but still valid; test via relationship)
        from ai_engine.capture import run_capture
        with tempfile.TemporaryDirectory() as tmp:
            # Empty relationships not valid? Actually empty is ok. Try duplicate id scenario
            # First remember
            r1 = run_capture({"text": "duplicate id test", "id": "custom_id_1", "type": "fact"}, project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            self.assertTrue(r1["ok"], r1)
            # Second with same id but different content should fail via dedup check
            r2 = run_capture({"text": "different content", "id": "custom_id_1", "type": "fact"}, project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            self.assertFalse(r2["ok"])
            self.assertIn("different content", r2["error"])

    def test_domain_neutral_vocabulary(self):
        from ai_engine.capture import run_capture
        with tempfile.TemporaryDirectory() as tmp:
            for t in ["fact", "observation", "insight", "routine", "preference"]:
                res = run_capture({"text": f"{t} content", "type": t}, project_id="default", data_root=tmp, vocabulary_id="diary_v1")
                self.assertTrue(res["ok"], f"type {t} should be valid: {res}")


class MemoryIntegrationViaCaptureTests(unittest.TestCase):
    def test_memory_remember_uses_generic_pipeline(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            # Memory.remember should delegate to capture pipeline and preserve same semantics
            r = mem.remember(text="memory via capture", type="fact")
            self.assertTrue(r["ok"], r)
            self.assertIn("activity_id", r)
            self.assertIn("context_id", r)
            # Activity stored in activity.db not engine_state
            from ai_engine.paths import get_activity_db
            self.assertTrue(os.path.exists(get_activity_db("default", tmp)))
            # Recall should find it via Ranker
            out = mem.recall(query="memory via capture")
            self.assertTrue(any(r["node_id"] == n["id"] for n in out["result"]["knowledge"]))

    def test_capture_and_memory_same_determinism(self):
        from ai_engine.capture import run_capture
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            raw = "determinism check"
            c1 = run_capture(raw, project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            # Second via Memory (different activity? Actually same raw same project should give same ids)
            # Use separate data_root to avoid collision? Use same tmp but different capture: second via Memory should be idempotent same ids
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            c2 = mem.remember(text=raw, type="fact")
            self.assertEqual(c1["activity_id"], c2["activity_id"])
            self.assertEqual(c1["node_id"], c2["node_id"])

    def test_no_plugin_registry_yet(self):
        # Phase 3: registry should not exist yet; Phase 6+: it exists but must be generic.
        # Phase 7+: plugins/code exists but must be generic.
        # Keep test compatible with later phases: if registry/plugins exists, verify generic.
        import os
        reg_path = os.path.join(_ROOT, "ai_engine", "registry.py")
        plugins_path = os.path.join(_ROOT, "ai_engine", "plugins")
        if os.path.exists(reg_path):
            with open(reg_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertIn("CaptureAdapter", content)
            # No hardcoded coding tool names in registry core
            # (registry may mention file.write in docstring but not as hardcoded registration)
            # Allow docstring mention but check not as registered effect
            self.assertNotIn("register_effect(\"file.write\"", content)
        if os.path.exists(plugins_path):
            # Phase 7+: plugins exists, verify it is isolated code domain, not core
            code_plugin = os.path.join(plugins_path, "code.py")
            if os.path.exists(code_plugin):
                with open(code_plugin, "r", encoding="utf-8") as f:
                    c = f.read()
                self.assertIn("CODE_SPECIFIC_MODULES", c)
                self.assertIn("tools/coding", c)

    def test_invariants_still_hold(self):
        from api.contract import CONTRACT_VERSION, OPERATIONS
        from tools.permissions.policy import HARD_WRITE_INVARIANTS
        import intelligence
        self.assertEqual(CONTRACT_VERSION, "1")
        self.assertEqual(tuple(OPERATIONS), ("search", "get", "related", "follow", "provenance", "inspect"))
        self.assertEqual(list(HARD_WRITE_INVARIANTS), [("database/knowledge.db", "blocked"), ("database/knowledge.db.backup", "blocked")])
        self.assertEqual(intelligence.__version__, "0")


if __name__ == "__main__":
    unittest.main()
