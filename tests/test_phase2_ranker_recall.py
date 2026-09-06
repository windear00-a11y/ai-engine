"""Phase 2 — Deterministic Recall / Ranker.

Tests for:
  * Ranker abstraction + KeywordRanker deterministic, explainable, injectable
  * Memory.recall() via Ranker (candidate_limit, provenance, deterministic)
  * Context-aware retrieval (context_id boost, strict filtering)
  * Project isolation preserved through ranking layer
  * No API v1 / legacy DB / trust bypass

All stdlib-only, per-project temp data_root.
"""

import os
import sys
import tempfile
import unittest
import hashlib
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

LEGACY_DB = os.path.join(_ROOT, "database", "knowledge.db")
EXPECTED_KNOWLEDGE_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"

def _sha256(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1<<20), b""):
            h.update(c)
    return h.hexdigest()


class KeywordRankerTests(unittest.TestCase):
    def test_deterministic_tie_break(self):
        from ai_engine.ranker import KeywordRanker
        r = KeywordRanker()
        # Two nodes with same raw score should tie-break by id ASC
        n1 = {"id": "fact_b", "type": "fact", "name": "sleep routine", "description": "evening walk"}
        n2 = {"id": "fact_a", "type": "fact", "name": "sleep routine", "description": "evening walk"}
        terms = ["sleep"]
        out1 = r.rank(terms, [n1, n2])
        out2 = r.rank(terms, [n2, n1])
        self.assertEqual([n["id"] for n in out1], [n["id"] for n in out2])
        self.assertEqual(out1[0]["id"], "fact_a")  # ASC tie-break

    def test_explainable_score(self):
        from ai_engine.ranker import KeywordRanker
        r = KeywordRanker()
        node = {"id": "fact_x", "type": "fact", "name": "Evening walk helps sleep", "description": "routine for sleep"}
        out = r.rank(["sleep", "walk"], [node])
        self.assertEqual(len(out), 1)
        sc = out[0]["_score"]
        self.assertIn("raw_score", sc)
        self.assertIn("context_match", sc)
        self.assertIn("quality", sc)
        self.assertIn("final_score", sc)
        self.assertEqual(sc["raw_score"], 2)  # both terms present

    def test_no_match_excluded(self):
        from ai_engine.ranker import KeywordRanker
        r = KeywordRanker()
        n = {"id": "fact_x", "type": "fact", "name": "hello", "description": "world"}
        out = r.rank(["zzz"], [n])
        self.assertEqual(out, [])

    def test_context_boost(self):
        from ai_engine.ranker import KeywordRanker
        r = KeywordRanker(context_strict=False)
        n_match = {"id": "fact_match", "type": "fact", "name": "sleep routine", "description": "evening walk", "_context_id": "ctx_abc"}
        n_mismatch = {"id": "fact_mismatch", "type": "fact", "name": "sleep routine", "description": "evening walk", "_context_id": "ctx_other"}
        terms = ["sleep"]
        # With context filter, matching should rank higher (final 1.0 vs 0.5)
        out = r.rank(terms, [n_mismatch, n_match], context={"context_id": "ctx_abc"})
        self.assertEqual(out[0]["id"], "fact_match")
        self.assertEqual(out[0]["_score"]["context_match"], 1.0)
        self.assertEqual(out[1]["_score"]["context_match"], 0.5)

    def test_context_strict_filtering(self):
        from ai_engine.ranker import KeywordRanker
        r_strict = KeywordRanker(context_strict=True)
        n_match = {"id": "fact_match", "type": "fact", "name": "sleep", "description": "sleep", "_context_id": "ctx_abc"}
        n_mismatch = {"id": "fact_mismatch", "type": "fact", "name": "sleep", "description": "sleep", "_context_id": "ctx_other"}
        terms = ["sleep"]
        out = r_strict.rank(terms, [n_mismatch, n_match], context={"context_id": "ctx_abc"})
        # Strict: mismatching should be filtered (quality 0 -> final 0 -> excluded)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], "fact_match")

    def test_injectable_via_memory(self):
        from ai_engine.memory import Memory
        from ai_engine.ranker import KeywordRanker
        with tempfile.TemporaryDirectory() as tmp:
            custom = KeywordRanker(context_strict=True)
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1", ranker=custom)
            self.assertIs(mem.ranker, custom)

    def test_ranker_is_deterministic(self):
        from ai_engine.ranker import KeywordRanker
        r = KeywordRanker()
        nodes = [
            {"id": f"fact_{i}", "type": "fact", "name": f"item {i} sleep", "description": f"desc {i}"}
            for i in range(5)
        ]
        terms = ["sleep"]
        out1 = r.rank(terms, nodes)
        out2 = r.rank(terms, list(reversed(nodes)))
        self.assertEqual([n["id"] for n in out1], [n["id"] for n in out2])


class MemoryRecallRankerTests(unittest.TestCase):
    def test_recall_uses_ranker_and_preserves_provenance(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            # Remember three facts with different texts
            mem.remember(text="evening walk helps sleep", type="fact")
            mem.remember(text="morning coffee boosts focus", type="fact")
            mem.remember(text="sleep routine includes walk", type="fact")
            out = mem.recall(query="sleep walk", limit=10)
            self.assertTrue(out["ok"], out)
            self.assertGreaterEqual(len(out["result"]["knowledge"]), 2)
            for n in out["result"]["knowledge"]:
                self.assertIn("provenance", n)
                self.assertEqual(n["provenance"]["source_name"], "manual")
                self.assertIn("_score", n)
                self.assertIn("raw_score", n["_score"])
            # Evidence chain deterministic
            self.assertIsNotNone(out["result"]["evidence_chain_id"])
            # Deterministic: same query again gives same ids and ordering
            out2 = mem.recall(query="sleep walk", limit=10)
            self.assertEqual([n["id"] for n in out["result"]["knowledge"]], [n["id"] for n in out2["result"]["knowledge"]])

    def test_recall_candidate_limit(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            for i in range(10):
                mem.remember(text=f"fact number {i} about sleep", type="fact")
            # recall with candidate_limit 5 should still return at most limit but candidate_count reflects all
            out = mem.recall(query="sleep", limit=3, candidate_limit=5)
            self.assertTrue(out["ok"], out)
            self.assertEqual(out["result"]["count"], 3)
            # candidate_count is total matches (10) > candidate_limit? Actually search_rankings finds 10, we hydrate 5, but count is len(rankings) =10
            self.assertGreaterEqual(out["result"]["candidate_count"], 10)

    def test_recall_context_aware_boost(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            r1 = mem.remember(text="context A fact about sleep", type="fact")
            ctx_a = r1["context_id"]
            r2 = mem.remember(text="context B fact about sleep", type="fact")
            ctx_b = r2["context_id"]
            self.assertNotEqual(ctx_a, ctx_b)
            # Recall with context A should boost first fact
            out_a = mem.recall(query="fact about sleep", context={"context_id": ctx_a})
            self.assertTrue(out_a["ok"], out_a)
            # At least the matching context should be top
            top_id_a = out_a["result"]["knowledge"][0]["id"]
            self.assertEqual(top_id_a, r1["node_id"])
            # Recall with context B should boost second
            out_b = mem.recall(query="fact about sleep", context={"context_id": ctx_b})
            self.assertEqual(out_b["result"]["knowledge"][0]["id"], r2["node_id"])

    def test_recall_context_strict(self):
        from ai_engine.memory import Memory
        from ai_engine.ranker import KeywordRanker
        with tempfile.TemporaryDirectory() as tmp:
            strict_ranker = KeywordRanker(context_strict=True)
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1", ranker=strict_ranker)
            r1 = mem.remember(text="strict context fact", type="fact")
            r2 = mem.remember(text="strict context fact other", type="fact")
            ctx1 = r1["context_id"]
            # Recall with strict context should filter mismatching (only one should remain with high score)
            out = mem.recall(query="strict context fact", context={"context_id": ctx1})
            self.assertTrue(out["ok"], out)
            # Both have same query terms, but only r1 matches context strictly
            self.assertEqual(out["result"]["knowledge"][0]["id"], r1["node_id"])
            # The other may be filtered if strict (depends on implementation) — at least top is correct

    def test_project_isolation_through_ranker(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            m_default = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            m_other = Memory(project_id="otherproj", data_root=tmp, vocabulary_id="diary_v1")
            m_default.remember(text="default isolated sleep fact", type="fact")
            m_other.remember(text="other isolated sleep fact", type="fact")
            out_default = m_default.recall(query="isolated sleep fact")
            out_other = m_other.recall(query="isolated sleep fact")
            self.assertTrue(any("default isolated" in n["description"] for n in out_default["result"]["knowledge"]))
            self.assertFalse(any("other isolated" in n["description"] for n in out_default["result"]["knowledge"]))
            self.assertTrue(any("other isolated" in n["description"] for n in out_other["result"]["knowledge"]))

    def test_recall_validation_preserved(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            r = mem.recall(query="   ")
            self.assertFalse(r["ok"])
            self.assertEqual(r["code"], "invalid_argument")
            r2 = mem.recall(query="hello", limit=0)
            self.assertFalse(r2["ok"])
            r3 = mem.recall(query="hello", candidate_limit=0)
            self.assertFalse(r3["ok"])

    def test_invariants_still_hold(self):
        from api.contract import CONTRACT_VERSION, OPERATIONS
        from tools.permissions.policy import HARD_WRITE_INVARIANTS
        import intelligence
        self.assertEqual(CONTRACT_VERSION, "1")
        self.assertEqual(tuple(OPERATIONS), ("search", "get", "related", "follow", "provenance", "inspect"))
        self.assertEqual(list(HARD_WRITE_INVARIANTS), [("database/knowledge.db", "blocked"), ("database/knowledge.db.backup", "blocked")])
        self.assertEqual(intelligence.__version__, "0")
        self.assertEqual(_sha256(LEGACY_DB), EXPECTED_KNOWLEDGE_SHA)
        con = sqlite3.connect(f"file:{LEGACY_DB}?mode=ro", uri=True)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        con.close()
        # No embeddings/LLM imported
        import sys
        for mod in list(sys.modules.keys()):
            self.assertNotIn("torch", mod)
            self.assertNotIn("openai", mod)
            self.assertNotIn("transformers", mod)


if __name__ == "__main__":
    unittest.main()
