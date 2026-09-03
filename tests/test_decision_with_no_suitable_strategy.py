"""Phase 7: system requests human guidance when no candidate meets threshold."""

import unittest

from intelligence.decision import decide, DecisionStore
from intelligence.reasoning.evidence_chain import build_chain
from intelligence.reasoning.types import Conclusion, EvidenceStep, ReasoningQuery
from intelligence.reasoning.schema import ReasoningOutput


def _ro(conf=0.81, ctx_id="ctx_a"):
    chain = build_chain([EvidenceStep("k1", "knowledge", "x", 0.9)])
    c = Conclusion(claim="x", confidence=conf, evidence_chain=chain,
                   context_id=ctx_id, source="knowledge")
    q = ReasoningQuery(question="q", task_type="bug_fix", domain="lint",
                       target={})
    return ReasoningOutput(reasoning_id="rs_a", query=q.to_dict(),
                           context_id=ctx_id, conclusions=[c],
                           contradictions=[], insufficient_evidence=[],
                           created_at_epoch=1.0)


class DecisionWithNoSuitableStrategyTests(unittest.TestCase):
    def test_empty_candidates_needs_guidance(self):
        ro = _ro()
        d = decide("t1", "bug_fix", [], reasoning_output=ro,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertIsNone(d.selected_strategy_id)
        self.assertEqual(d.status, "needs_guidance")
        self.assertTrue(d.approval_required)
        self.assertEqual(d.kind, "guidance")

    def test_below_min_confidence_needs_guidance(self):
        # decision confidence 0.2 < MIN_CONFIDENCE 0.35
        ro_low = _ro(conf=0.2)
        candidates = [
            {"strategy_id": "st_a", "name": "a", "problem_class": "bug_fix",
             "tool_sequence": ["file.read"], "confidence": 0.9},
        ]
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro_low,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertIsNone(d.selected_strategy_id)
        self.assertEqual(d.status, "needs_guidance")

    def test_all_filtered_by_policy_needs_guidance(self):
        ro = _ro()
        candidates = [
            {"strategy_id": "st_del", "name": "del", "problem_class": "bug_fix",
             "tool_sequence": ["file.delete"], "confidence": 0.9},
        ]
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertIsNone(d.selected_strategy_id)
        self.assertEqual(d.status, "needs_guidance")

    def test_guidance_decision_is_persisted(self):
        candidates = [
            {"strategy_id": "st_a", "name": "a", "problem_class": "bug_fix",
             "tool_sequence": ["file.read"], "confidence": 0.9},
        ]
        store = DecisionStore(":memory:")
        self.addCleanup(store.close)
        ro2 = _ro(conf=0.2)
        d2 = decide("t2", "bug_fix", candidates, reasoning_output=ro2,
                    context={"context_id": "ctx_a"}, store=store,
                    created_at_epoch=2.0)
        self.assertEqual(d2.status, "needs_guidance")
        self.assertIsNotNone(store.get(d2.decision_id))

    def test_rationale_explains_guidance(self):
        ro = _ro(conf=0.2)
        candidates = [
            {"strategy_id": "st_a", "name": "a", "problem_class": "bug_fix",
             "tool_sequence": ["file.read"], "confidence": 0.9},
        ]
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertIn("no_suitable_strategy", d.rationale.get("reason", ""))
