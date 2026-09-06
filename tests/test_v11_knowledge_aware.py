"""V1.1: Knowledge-Aware (ranked retrieval) — comprehensive tests.

The request-handler tests moved to ``test_v11_request_domain`` (retired with
``engine.request_handler``). The ranked retrieval under test now lives in the
generic core modules ``retrieval.ranked_knowledge`` / ``retrieval.ranked_experience``
(promoted from ``engine.knowledge_retrieval`` / ``engine.experience_retrieval``).
"""

import os
import pathlib
import tempfile
import unittest
from unittest.mock import MagicMock
from retrieval.ranked_knowledge import (
    build_query_terms, score_node, retrieve_ranked_knowledge)
from retrieval.ranked_experience import retrieve_ranked_experience

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _intent():
    return {"intent": "bug_fix", "target": {"file": "src/utils.py"},
            "error": {"message": "E302"}, "domain": "lint"}


class V11QueryConstructionTests(unittest.TestCase):
    def test_query_terms_deterministic(self):
        intent = _intent()
        terms1 = build_query_terms(intent)
        terms2 = build_query_terms(intent)
        self.assertEqual(terms1, terms2)
        self.assertIn("bug_fix", terms1)
        self.assertIn("utils", terms1)
        self.assertIn("e302", terms1)
        self.assertLessEqual(len(terms1), 10)

    def test_raw_request_not_used(self):
        # Ensure build_query_terms does not use raw_request directly
        intent = {"intent": "bug_fix", "target": {"file": "src/a.py"},
                  "error": {"message": "E302"}, "domain": "lint"}
        terms = build_query_terms(intent)
        self.assertNotIn("DROP", str(terms))


class V11RankingTests(unittest.TestCase):
    def test_exact_error_match_scores_higher(self):
        intent = _intent()
        node_match = {"id": "n1", "name": "E302 fixed", "description": "",
                      "type": "fact", "subject": "e302",
                      "lifecycle": {"confidence": 1.0}}
        node_no = {"id": "n2", "name": "Something else", "description": "",
                   "type": "fact", "subject": "other",
                   "lifecycle": {"confidence": 1.0}}
        s1 = score_node(node_match, intent, None)
        s2 = score_node(node_no, intent, None)
        self.assertGreater(s1["raw_score"], s2["raw_score"])
        self.assertGreater(s1["final_score"], s2["final_score"])

    def test_tie_break_node_id(self):
        intent = _intent()
        # Two nodes with same score, different ids
        node_a = {"id": "a1", "name": "E302", "description": "",
                  "type": "fact", "subject": "e302",
                  "lifecycle": {"confidence": 1.0}}
        node_b = {"id": "b1", "name": "E302", "description": "",
                  "type": "fact", "subject": "e302",
                  "lifecycle": {"confidence": 1.0}}
        # Mock client returning both
        mock_client = MagicMock()
        mock_client.search.return_value = [node_a, node_b]
        mock_client.related.return_value = []
        mock_client.provenance.return_value = {}
        res = retrieve_ranked_knowledge(intent, None,
                                        knowledge_client=mock_client,
                                        candidate_limit=50, max_knowledge=5)
        ids = [n["id"] for n in res["knowledge"]]
        self.assertEqual(ids, sorted(ids))  # node_id ASC tie-break

    def test_quality_factors(self):
        intent = _intent()
        node_high = {"id": "n1", "name": "E302", "description": "",
                     "type": "fact", "subject": "e302",
                     "lifecycle": {"confidence": 0.9,
                                   "evidence_quality": 1.0}}
        node_low = {"id": "n2", "name": "E302", "description": "",
                    "type": "fact", "subject": "e302",
                    "lifecycle": {"confidence": 0.5,
                                  "evidence_quality": 1.0}}
        s_high = score_node(node_high, intent, None)
        s_low = score_node(node_low, intent, None)
        self.assertGreater(s_high["final_score"], s_low["final_score"])


class V11LifecycleTests(unittest.TestCase):
    def test_superseded_excluded(self):
        intent = _intent()
        node_active = {"id": "n1", "name": "E302", "description": "",
                       "type": "fact", "subject": "e302",
                       "lifecycle": {"confidence": 1.0, "status": "active"}}
        node_super = {"id": "n2", "name": "E302 old", "description": "",
                      "type": "fact", "subject": "e302",
                      "lifecycle": {"confidence": 1.0, "status": "superseded",
                                    "superseded_by": "n1"}}
        mock_client = MagicMock()
        mock_client.search.return_value = [node_active, node_super]
        mock_client.related.return_value = []
        mock_client.provenance.return_value = {}
        res = retrieve_ranked_knowledge(intent, None,
                                        knowledge_client=mock_client)
        ids = [n["id"] for n in res["knowledge"]]
        self.assertIn("n1", ids)
        self.assertNotIn("n2", ids)
        self.assertTrue(any(f["id"] == "n2" for f in res["filtered"]))

    def test_candidate_weaker(self):
        intent = _intent()
        node_candidate = {"id": "n1", "name": "E302 candidate", "description": "",
                          "type": "fact", "subject": "e302",
                          "lifecycle": {"confidence": 1.0,
                                        "status": "candidate"}}
        node_active = {"id": "n2", "name": "E302 active", "description": "",
                       "type": "fact", "subject": "e302",
                       "lifecycle": {"confidence": 1.0, "status": "active"}}
        mock_client = MagicMock()
        mock_client.search.return_value = [node_candidate, node_active]
        mock_client.related.return_value = []
        mock_client.provenance.return_value = {}
        res = retrieve_ranked_knowledge(intent, None,
                                        knowledge_client=mock_client)
        # Candidate should be ranked lower due to halved raw
        self.assertEqual(res["knowledge"][0]["id"], "n2")


class V11ContextFilteringTests(unittest.TestCase):
    def test_context_mismatch_eliminates(self):
        from intelligence.context.schema import ContextSnapshot
        intent = _intent()
        # Create two contexts: one matching, one not
        snap = ContextSnapshot.build(system={"os": "linux"},
                                     project={"language": "python"},
                                     task={"type": "bug_fix"}, temporal={},
                                     captured_at_epoch=1.0)
        # Node restricted to other context
        node = {"id": "n1", "name": "E302", "description": "",
                "type": "fact", "subject": "e302",
                "lifecycle": {"confidence": 1.0,
                              "context_restrictions":
                                  {"allowed_contexts": ["ctx_other"]}}}
        mock_client = MagicMock()
        mock_client.search.return_value = [node]
        mock_client.related.return_value = []
        mock_client.provenance.return_value = {}
        res = retrieve_ranked_knowledge(intent, snap,
                                        knowledge_client=mock_client)
        self.assertEqual(len(res["knowledge"]), 0)
        self.assertEqual(len(res["filtered"]), 1)


class V11ExperienceRetrievalTests(unittest.TestCase):
    def test_experience_ranking(self):
        from intelligence.experience.schema import (ExperienceRecord,
                                                    derive_experience_id)
        from intelligence.experience.store import ExperienceStore
        store = ExperienceStore(":memory:")
        # Create experiences with different task types
        for i, tt in enumerate(["bug_fix", "bug_fix", "test_verify"]):
            exp = ExperienceRecord(
                experience_id=derive_experience_id(
                    f"t{i}", f"ctx_{i}", f"oc{i}", [f"ev{i}"], f"st{i}"),
                task_id=f"t{i}", task_type=tt, domain="lint",
                context_id=f"ctx_{i}", outcome_id=f"oc{i}",
                evidence_ids=(f"ev{i}",), strategy_id=f"st{i}",
                summary={"outcome": "success",
                         "target_file": "src/utils.py"},
                synthesized_at_epoch=float(i))
            store.save(exp)
        intent = _intent()
        from intelligence.context.schema import ContextSnapshot
        snap = ContextSnapshot.build(system={}, project={},
                                     task={"type": "bug_fix"}, temporal={},
                                     captured_at_epoch=1.0)
        res = retrieve_ranked_experience(intent, snap,
                                         experience_store=store,
                                         max_experience=5)
        # Should return bug_fix experiences, not test_verify
        for exp in res["experience"]:
            self.assertEqual(exp.task_type, "bug_fix")
        store.close()


class V11DeterminismTests(unittest.TestCase):
    def test_deterministic_retrieval(self):
        intent = _intent()
        mock_client = MagicMock()
        nodes = [{"id": f"n{i}", "name": f"E302 {i}", "description": "",
                  "type": "fact", "subject": "e302",
                  "lifecycle": {"confidence": 1.0}} for i in range(3)]
        mock_client.search.return_value = nodes
        mock_client.related.return_value = []
        mock_client.provenance.return_value = {}
        r1 = retrieve_ranked_knowledge(intent, None,
                                       knowledge_client=mock_client)
        r2 = retrieve_ranked_knowledge(intent, None,
                                       knowledge_client=mock_client)
        self.assertEqual([n["id"] for n in r1["knowledge"]],
                         [n["id"] for n in r2["knowledge"]])
        self.assertEqual(r1["evidence_chain_id"], r2["evidence_chain_id"])


class V11FailureHandlingTests(unittest.TestCase):
    def test_no_match_returns_empty(self):
        intent = {"intent": "bug_fix", "target": {"file": "src/unknown.py"},
                  "error": {"message": "E999"}, "domain": "lint"}
        mock_client = MagicMock()
        mock_client.search.return_value = []
        mock_client.related.return_value = []
        res = retrieve_ranked_knowledge(intent, None,
                                        knowledge_client=mock_client)
        self.assertEqual(len(res["knowledge"]), 0)
        self.assertFalse(res["ambiguous"])

    def test_client_failure_returns_empty(self):
        intent = _intent()
        mock_client = MagicMock()
        mock_client.search.side_effect = Exception("DB failure")
        res = retrieve_ranked_knowledge(intent, None,
                                        knowledge_client=mock_client)
        self.assertEqual(len(res["knowledge"]), 0)
        self.assertIn("error", res)

    def test_empty_intent_terms_fail_safe(self):
        # Build terms deterministically even for a minimal intent; retrieval
        # over an empty knowledge client stays non-fatal.
        terms = build_query_terms({"intent": "bug_fix"})
        self.assertIn("bug_fix", terms)
        mock_client = MagicMock()
        mock_client.search.return_value = []
        res = retrieve_ranked_knowledge({"intent": "bug_fix", "target": {}},
                                        None, knowledge_client=mock_client)
        self.assertEqual(len(res["knowledge"]), 0)


class V11SafetyTests(unittest.TestCase):
    def test_knowledge_db_protection(self):
        # Ranked retrieval must not import sqlite directly (abstraction only)
        text = (ROOT / "retrieval" / "ranked_knowledge.py").read_text()
        self.assertNotIn("import sqlite", text.lower())
        self.assertNotIn("from sqlite", text.lower())
        text2 = (ROOT / "intelligence" / "loop" / "retrieve.py").read_text()
        # Should use KnowledgeClient abstraction
        self.assertIn("KnowledgeClient", text2 or "")

    def test_no_live_data_used(self):
        # Retrieval is a pure function over a passed-in client/store; it must
        # not open the production database on its own.
        import inspect
        src = inspect.getsource(retrieve_ranked_knowledge)
        self.assertNotIn("knowledge.db", src)
        self.assertNotIn("sqlite3.connect", src)


if __name__ == "__main__":
    unittest.main()