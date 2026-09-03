"""Phase 10: intelligence API operations work."""

import unittest

from intelligence.api import IntelligenceToolInterface
from intelligence.experience.store import ExperienceStore
from intelligence.experience.schema import ExperienceRecord, derive_experience_id
from intelligence.decision.store import DecisionStore
from intelligence.decision import decide
from intelligence.reasoning.evidence_chain import build_chain
from intelligence.reasoning.types import Conclusion, EvidenceStep, ReasoningQuery
from intelligence.reasoning.schema import ReasoningOutput
from intelligence.learning.store import LearningStore
from intelligence.context.store import ContextStore
from intelligence.context.capture import capture_context


def _setup_api():
    exp_store = ExperienceStore(":memory:")
    dec_store = DecisionStore(":memory:")
    learn_store = LearningStore(":memory:")
    ctx_store = ContextStore(":memory:")
    # seed experience
    rec = ExperienceRecord(
        experience_id=derive_experience_id("t1", "ctx_a", "oc1", ["ev1"], "st1"),
        task_id="t1", task_type="bug_fix", domain="lint", context_id="ctx_a",
        outcome_id="oc1", evidence_ids=("ev1",), strategy_id="st1",
        summary={}, synthesized_at_epoch=1.0)
    exp_store.save(rec)
    # seed decision
    chain = build_chain([EvidenceStep("k1", "knowledge", "x", 0.9)])
    q = ReasoningQuery(question="q", task_type="bug_fix", domain="lint", target={})
    c = Conclusion(claim="x", confidence=0.8, evidence_chain=chain, context_id="ctx_a", source="knowledge")
    ro = ReasoningOutput(reasoning_id="rs_a", query=q.to_dict(), context_id="ctx_a",
                         conclusions=[c], contradictions=[], insufficient_evidence=[], created_at_epoch=1.0)
    candidates = [{"strategy_id": "st1", "name": "a", "problem_class": "bug_fix",
                   "tool_sequence": ["file.read"], "confidence": 0.8}]
    dec = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                 context={"context_id": "ctx_a"}, store=dec_store, created_at_epoch=1.0)
    # seed context
    snap = capture_context(project_root=None, task_metadata={"type": "bug_fix", "domain": "lint"})
    ctx_store.save(snap)
    api = IntelligenceToolInterface(
        experience_store=exp_store, decision_store=dec_store,
        learning_store=learn_store, context_store=ctx_store)
    return api, exp_store, dec_store, learn_store, ctx_store, rec, dec, snap


class IntelligenceAPIOperationsTests(unittest.TestCase):
    def test_experience_search(self):
        api, *_ = _setup_api()
        res = api.execute({"operation": "experience.search", "arguments": {"task_type": "bug_fix", "limit": 10}})
        self.assertTrue(res["ok"])
        self.assertGreater(len(res["result"]), 0)
        self.assertEqual(res["contract_version"], "1")

    def test_experience_get(self):
        api, exp_store, dec_store, learn_store, ctx_store, rec, dec, snap = _setup_api()
        res = api.execute({"operation": "experience.get", "arguments": {"experience_id": rec.experience_id}})
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"]["experience_id"], rec.experience_id)

    def test_decision_get(self):
        api, exp_store, dec_store, learn_store, ctx_store, rec, dec, snap = _setup_api()
        res = api.execute({"operation": "decision.get", "arguments": {"decision_id": dec.decision_id}})
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"]["decision_id"], dec.decision_id)

    def test_decision_audit(self):
        api, *_ = _setup_api()
        res = api.execute({"operation": "decision.audit", "arguments": {"limit": 5}})
        self.assertTrue(res["ok"])
        self.assertIsInstance(res["result"], list)

    def test_learning_events(self):
        api, *_ = _setup_api()
        res = api.execute({"operation": "learning.events", "arguments": {"limit": 5}})
        self.assertTrue(res["ok"])
        self.assertIsInstance(res["result"], list)

    def test_context_get(self):
        api, exp_store, dec_store, learn_store, ctx_store, rec, dec, snap = _setup_api()
        res = api.execute({"operation": "context.get", "arguments": {"context_id": snap.context_id}})
        self.assertTrue(res["ok"])
        self.assertEqual(res["result"]["context_id"], snap.context_id)

    def test_context_similar(self):
        api, exp_store, dec_store, learn_store, ctx_store, rec, dec, snap = _setup_api()
        # Add another context
        snap2 = capture_context(project_root=None, task_metadata={"type": "bug_fix", "domain": "lint"})
        # Ensure different id (likely same due to deterministic, so modify task)
        snap2 = capture_context(project_root=None, task_metadata={"type": "test_verify", "domain": "lint"})
        ctx_store.save(snap2)
        res = api.execute({"operation": "context.similar", "arguments": {"context_id": snap.context_id, "limit": 2}})
        self.assertTrue(res["ok"])
        self.assertIsInstance(res["result"], list)

    def test_knowledge_confidence_not_found(self):
        api, *_ = _setup_api()
        res = api.execute({"operation": "knowledge.confidence", "arguments": {"node_id": "nonexistent_xyz"}})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "not_found")

    def test_knowledge_lifecycle_not_found(self):
        api, *_ = _setup_api()
        res = api.execute({"operation": "knowledge.lifecycle", "arguments": {"node_id": "nonexistent_xyz"}})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "not_found")
