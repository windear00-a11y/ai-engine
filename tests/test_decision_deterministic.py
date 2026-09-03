"""Phase 7: decision is deterministic (same inputs -> same outputs)."""

import unittest

from intelligence.decision import decide, DecisionStore
from intelligence.decision.store import DecisionStore as DS
from intelligence.reasoning.evidence_chain import build_chain
from intelligence.reasoning.types import Conclusion, EvidenceStep
from intelligence.reasoning.schema import ReasoningOutput
from intelligence.reasoning.types import ReasoningQuery


def _ro(conf=0.81, ctx_id="ctx_a"):
    chain = build_chain([EvidenceStep("k1", "knowledge", "x", 0.9)])
    c = Conclusion(claim="bug_fix appropriate", confidence=conf,
                   evidence_chain=chain, context_id=ctx_id, source="knowledge")
    q = ReasoningQuery(question="q", task_type="bug_fix", domain="lint",
                       target={})
    return ReasoningOutput(reasoning_id="rs_" + "a" * 32, query=q.to_dict(),
                           context_id=ctx_id, conclusions=[c],
                           contradictions=[], insufficient_evidence=[],
                           created_at_epoch=1.0)


CANDIDATES = [
    {"strategy_id": "st_a", "name": "bug_fix", "problem_class": "bug_fix",
     "tool_sequence": ["file.read", "file.diff"], "confidence": 0.8},
    {"strategy_id": "st_b", "name": "test_verify", "problem_class": "bug_fix",
     "tool_sequence": ["file.read", "project.test"], "confidence": 0.6},
]
CTX = {"context_id": "ctx_a"}


class DecisionDeterministicTests(unittest.TestCase):
    def test_identical_inputs_identical_output(self):
        store = DS(":memory:")
        self.addCleanup(store.close)
        ro = _ro()
        a = decide("t1", "bug_fix", list(CANDIDATES), reasoning_output=ro,
                   context=CTX, store=store, created_at_epoch=1.0)
        b = decide("t1", "bug_fix", list(CANDIDATES), reasoning_output=ro,
                   context=CTX, store=store, created_at_epoch=1.0)
        self.assertEqual(a.decision_id, b.decision_id)
        self.assertEqual(a.selected_strategy_id, b.selected_strategy_id)
        self.assertEqual(a.confidence, b.confidence)
        self.assertEqual(a.rationale, b.rationale)

    def test_decision_id_prefix(self):
        ro = _ro()
        d = decide("t1", "bug_fix", list(CANDIDATES), reasoning_output=ro,
                   context=CTX, store=DecisionStore(":memory:"),
                   created_at_epoch=1.0)
        self.assertTrue(d.decision_id.startswith("ds_"))
        self.assertEqual(len(d.decision_id), 3 + 64)

    def test_different_task_different_id(self):
        ro = _ro()
        a = decide("t1", "bug_fix", list(CANDIDATES), reasoning_output=ro,
                   context=CTX, store=DecisionStore(":memory:"),
                   created_at_epoch=1.0)
        b = decide("t2", "bug_fix", list(CANDIDATES), reasoning_output=ro,
                   context=CTX, store=DecisionStore(":memory:"),
                   created_at_epoch=1.0)
        self.assertNotEqual(a.decision_id, b.decision_id)
        self.assertEqual(a.selected_strategy_id, b.selected_strategy_id)

    def test_output_serializable(self):
        ro = _ro()
        d = decide("t1", "bug_fix", list(CANDIDATES), reasoning_output=ro,
                   context=CTX, store=DecisionStore(":memory:"),
                   created_at_epoch=1.0)
        dic = d.to_dict()
        self.assertIn("decision_id", dic)
        self.assertIn("selected_strategy_id", dic)
        self.assertIn("rationale", dic)
        self.assertIn("alternatives_rejected", dic)
