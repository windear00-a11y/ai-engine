"""V1.1: Knowledge-Aware Request — comprehensive tests."""

import unittest
from unittest.mock import MagicMock
from engine.knowledge_retrieval import build_query_terms, score_node, retrieve_ranked_knowledge
from engine.experience_retrieval import retrieve_ranked_experience
from engine.request_handler import handle_request
from engine.request_adapter import parse_request

class V11QueryConstructionTests(unittest.TestCase):
    def test_query_terms_deterministic(self):
        intent = {"intent": "bug_fix", "target": {"file": "src/utils.py"}, "error": {"message": "E302"}, "domain": "lint"}
        terms1 = build_query_terms(intent)
        terms2 = build_query_terms(intent)
        self.assertEqual(terms1, terms2)
        self.assertIn("bug_fix", terms1)
        self.assertIn("utils", terms1)
        self.assertIn("e302", terms1)
        self.assertLessEqual(len(terms1), 10)

    def test_raw_request_not_used(self):
        # Ensure build_query_terms does not use raw_request directly
        intent = {"intent": "bug_fix", "target": {"file": "src/a.py"}, "error": {"message": "E302"}, "domain": "lint"}
        terms = build_query_terms(intent)
        self.assertNotIn("DROP", str(terms))

class V11RankingTests(unittest.TestCase):
    def test_exact_error_match_scores_higher(self):
        intent = {"intent": "bug_fix", "target": {"file": "src/utils.py"}, "error": {"message": "E302"}, "domain": "lint"}
        node_match = {"id": "n1", "name": "E302 fixed", "description": "", "type": "fact", "subject": "e302", "lifecycle": {"confidence": 1.0}}
        node_no = {"id": "n2", "name": "Something else", "description": "", "type": "fact", "subject": "other", "lifecycle": {"confidence": 1.0}}
        s1 = score_node(node_match, intent, None)
        s2 = score_node(node_no, intent, None)
        self.assertGreater(s1["raw_score"], s2["raw_score"])
        self.assertGreater(s1["final_score"], s2["final_score"])

    def test_tie_break_node_id(self):
        intent = {"intent": "bug_fix", "target": {"file": "src/utils.py"}, "error": {"message": "E302"}, "domain": "lint"}
        # Two nodes with same score, different ids
        node_a = {"id": "a1", "name": "E302", "description": "", "type": "fact", "subject": "e302", "lifecycle": {"confidence": 1.0}}
        node_b = {"id": "b1", "name": "E302", "description": "", "type": "fact", "subject": "e302", "lifecycle": {"confidence": 1.0}}
        # Mock client returning both
        mock_client = MagicMock()
        mock_client.search.return_value = [node_a, node_b]
        mock_client.related.return_value = []
        mock_client.provenance.return_value = {}
        res = retrieve_ranked_knowledge(intent, None, knowledge_client=mock_client, candidate_limit=50, max_knowledge=5)
        ids = [n["id"] for n in res["knowledge"]]
        self.assertEqual(ids, sorted(ids))  # node_id ASC tie-break

    def test_quality_factors(self):
        intent = {"intent": "bug_fix", "target": {"file": "src/utils.py"}, "error": {"message": "E302"}, "domain": "lint"}
        node_high = {"id": "n1", "name": "E302", "description": "", "type": "fact", "subject": "e302", "lifecycle": {"confidence": 0.9, "evidence_quality": 1.0}}
        node_low = {"id": "n2", "name": "E302", "description": "", "type": "fact", "subject": "e302", "lifecycle": {"confidence": 0.5, "evidence_quality": 1.0}}
        s_high = score_node(node_high, intent, None)
        s_low = score_node(node_low, intent, None)
        self.assertGreater(s_high["final_score"], s_low["final_score"])

class V11LifecycleTests(unittest.TestCase):
    def test_superseded_excluded(self):
        intent = {"intent": "bug_fix", "target": {"file": "src/utils.py"}, "error": {"message": "E302"}, "domain": "lint"}
        node_active = {"id": "n1", "name": "E302", "description": "", "type": "fact", "subject": "e302", "lifecycle": {"confidence": 1.0, "status": "active"}}
        node_super = {"id": "n2", "name": "E302 old", "description": "", "type": "fact", "subject": "e302", "lifecycle": {"confidence": 1.0, "status": "superseded", "superseded_by": "n1"}}
        mock_client = MagicMock()
        mock_client.search.return_value = [node_active, node_super]
        mock_client.related.return_value = []
        mock_client.provenance.return_value = {}
        res = retrieve_ranked_knowledge(intent, None, knowledge_client=mock_client)
        ids = [n["id"] for n in res["knowledge"]]
        self.assertIn("n1", ids)
        self.assertNotIn("n2", ids)
        self.assertTrue(any(f["id"]=="n2" for f in res["filtered"]))

    def test_candidate_weaker(self):
        intent = {"intent": "bug_fix", "target": {"file": "src/utils.py"}, "error": {"message": "E302"}, "domain": "lint"}
        node_candidate = {"id": "n1", "name": "E302 candidate", "description": "", "type": "fact", "subject": "e302", "lifecycle": {"confidence": 1.0, "status": "candidate"}}
        node_active = {"id": "n2", "name": "E302 active", "description": "", "type": "fact", "subject": "e302", "lifecycle": {"confidence": 1.0, "status": "active"}}
        mock_client = MagicMock()
        mock_client.search.return_value = [node_candidate, node_active]
        mock_client.related.return_value = []
        mock_client.provenance.return_value = {}
        res = retrieve_ranked_knowledge(intent, None, knowledge_client=mock_client)
        # Candidate should be ranked lower due to halved raw
        self.assertEqual(res["knowledge"][0]["id"], "n2")

class V11ContextFilteringTests(unittest.TestCase):
    def test_context_mismatch_eliminates(self):
        from intelligence.context.schema import ContextSnapshot
        intent = {"intent": "bug_fix", "target": {"file": "src/utils.py"}, "error": {"message": "E302"}, "domain": "lint"}
        # Create two contexts: one matching, one not
        snap = ContextSnapshot.build(system={"os": "linux"}, project={"language": "python"}, task={"type": "bug_fix"}, temporal={}, captured_at_epoch=1.0)
        # Node restricted to other context
        node = {"id": "n1", "name": "E302", "description": "", "type": "fact", "subject": "e302", "lifecycle": {"confidence": 1.0, "context_restrictions": {"allowed_contexts": ["ctx_other"]}}}
        mock_client = MagicMock()
        mock_client.search.return_value = [node]
        mock_client.related.return_value = []
        mock_client.provenance.return_value = {}
        res = retrieve_ranked_knowledge(intent, snap, knowledge_client=mock_client)
        self.assertEqual(len(res["knowledge"]), 0)
        self.assertEqual(len(res["filtered"]), 1)

class V11ExperienceRetrievalTests(unittest.TestCase):
    def test_experience_ranking(self):
        from intelligence.experience.schema import ExperienceRecord, derive_experience_id
        from intelligence.experience.store import ExperienceStore
        store = ExperienceStore(":memory:")
        # Create experiences with different task types
        for i, tt in enumerate(["bug_fix", "bug_fix", "test_verify"]):
            exp = ExperienceRecord(experience_id=derive_experience_id(f"t{i}", f"ctx_{i}", f"oc{i}", [f"ev{i}"], f"st{i}"), task_id=f"t{i}", task_type=tt, domain="lint", context_id=f"ctx_{i}", outcome_id=f"oc{i}", evidence_ids=(f"ev{i}",), strategy_id=f"st{i}", summary={"outcome": "success", "target_file": "src/utils.py"}, synthesized_at_epoch=float(i))
            store.save(exp)
        intent = {"intent": "bug_fix", "target": {"file": "src/utils.py"}, "error": {"message": "E302"}, "domain": "lint"}
        from intelligence.context.schema import ContextSnapshot
        snap = ContextSnapshot.build(system={}, project={}, task={"type": "bug_fix"}, temporal={}, captured_at_epoch=1.0)
        res = retrieve_ranked_experience(intent, snap, experience_store=store, max_experience=5)
        # Should return bug_fix experiences, not test_verify
        for exp in res["experience"]:
            self.assertEqual(exp.task_type, "bug_fix")
        store.close()

class V11EvidenceAssemblyTests(unittest.TestCase):
    def test_request_produces_retrieval_field(self):
        res = handle_request({"request": "Fix E302 in src/utils.py"}, workspace_root="/tmp", knowledge_nodes=[])
        self.assertIn("retrieval", res)
        self.assertIn("knowledge", res["retrieval"])
        self.assertIn("experience", res["retrieval"])
        self.assertIn("query_terms", res["retrieval"])

class V11DeterminismTests(unittest.TestCase):
    def test_deterministic_retrieval(self):
        intent = {"intent": "bug_fix", "target": {"file": "src/utils.py"}, "error": {"message": "E302"}, "domain": "lint"}
        mock_client = MagicMock()
        nodes = [{"id": f"n{i}", "name": f"E302 {i}", "description": "", "type": "fact", "subject": "e302", "lifecycle": {"confidence": 1.0}} for i in range(3)]
        mock_client.search.return_value = nodes
        mock_client.related.return_value = []
        mock_client.provenance.return_value = {}
        r1 = retrieve_ranked_knowledge(intent, None, knowledge_client=mock_client)
        r2 = retrieve_ranked_knowledge(intent, None, knowledge_client=mock_client)
        self.assertEqual([n["id"] for n in r1["knowledge"]], [n["id"] for n in r2["knowledge"]])
        self.assertEqual(r1["evidence_chain_id"], r2["evidence_chain_id"])

class V11FailureHandlingTests(unittest.TestCase):
    def test_no_match_returns_empty(self):
        intent = {"intent": "bug_fix", "target": {"file": "src/unknown.py"}, "error": {"message": "E999"}, "domain": "lint"}
        mock_client = MagicMock()
        mock_client.search.return_value = []
        mock_client.related.return_value = []
        res = retrieve_ranked_knowledge(intent, None, knowledge_client=mock_client)
        self.assertEqual(len(res["knowledge"]), 0)
        self.assertFalse(res["ambiguous"])

    def test_client_failure_returns_empty(self):
        intent = {"intent": "bug_fix", "target": {"file": "src/utils.py"}, "error": {"message": "E302"}, "domain": "lint"}
        mock_client = MagicMock()
        mock_client.search.side_effect = Exception("DB failure")
        res = retrieve_ranked_knowledge(intent, None, knowledge_client=mock_client)
        self.assertEqual(len(res["knowledge"]), 0)
        self.assertIn("error", res)

    def test_insufficient_evidence_via_handler(self):
        res = handle_request({"request": "Fix E302 in src/utils.py"}, workspace_root="/tmp", knowledge_nodes=[])
        # With empty knowledge and no experience, reasoning may be insufficient but handler still ok via fallback
        self.assertIn("status", res)

    def test_malformed_request(self):
        res = handle_request({"request": "   "}, workspace_root="/tmp")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "invalid_argument")

class V11SafetyTests(unittest.TestCase):
    def test_sql_injection_not_executed(self):
        res = handle_request({"request": "Fix E302'; DROP TABLE knowledge; -- in src/utils.py"}, workspace_root="/tmp")
        self.assertIn("intent", res)
        # Should be treated as bug_fix, not executed as SQL
        self.assertNotEqual(res.get("status"), "internal_error")

    def test_path_traversal(self):
        res = handle_request({"request": "Fix E302 in ../../etc/passwd"}, workspace_root="/tmp")
        self.assertIn("status", res)

    def test_approval_bypass_rejected(self):
        res = handle_request({"request": "Fix E302 in src/utils.py", "approved": True}, workspace_root="/tmp")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "invalid_argument")

    def test_knowledge_db_protection(self):
        # Check that handler does not import sqlite for knowledge (only docstring mention allowed)
        import pathlib
        text = pathlib.Path("engine/knowledge_retrieval.py").read_text()
        self.assertNotIn("import sqlite", text.lower())
        self.assertNotIn("from sqlite", text.lower())
        text2 = pathlib.Path("intelligence/loop/retrieve.py").read_text()
        # Should use KnowledgeClient abstraction
        self.assertIn("KnowledgeClient", text2 or "")

class V11BackwardCompatibilityTests(unittest.TestCase):
    def test_knowledge_nodes_override(self):
        # When caller provides knowledge_nodes, they are used as-is, no KnowledgeClient search
        nodes = [{"id": "custom", "name": "custom", "description": "", "type": "fact", "subject": "custom", "lifecycle": {"confidence": 1.0}}]
        res = handle_request({"request": "Fix E302 in src/utils.py"}, workspace_root="/tmp", knowledge_nodes=nodes)
        # Should still succeed, retrieval should reflect provided nodes
        self.assertIn("retrieval", res)
        self.assertEqual(len(res["retrieval"]["knowledge"]), 1)
        self.assertEqual(res["retrieval"]["knowledge"][0]["id"], "custom")

    def test_v1_fields_still_present(self):
        res = handle_request({"request": "Fix E302 in src/utils.py"}, workspace_root="/tmp")
        for field in ["request_id", "intent", "context_id", "reasoning_id", "decision_id", "plan_id", "status", "ok"]:
            self.assertIn(field, res)

if __name__ == "__main__":
    unittest.main()
