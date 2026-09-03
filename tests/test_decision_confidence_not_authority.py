"""Phase 7: high confidence does not bypass policy (D5)."""

import unittest

from intelligence.decision import decide, DecisionStore, Policy, PolicyEngine
from intelligence.reasoning.evidence_chain import build_chain
from intelligence.reasoning.types import Conclusion, EvidenceStep, ReasoningQuery
from intelligence.reasoning.schema import ReasoningOutput


def _ro(conf=0.95, ctx_id="ctx_a"):
    chain = build_chain([EvidenceStep("k1", "knowledge", "x", 0.95)])
    c = Conclusion(claim="x", confidence=conf, evidence_chain=chain,
                   context_id=ctx_id, source="knowledge")
    q = ReasoningQuery(question="q", task_type="bug_fix", domain="lint",
                       target={})
    return ReasoningOutput(reasoning_id="rs_a", query=q.to_dict(),
                           context_id=ctx_id, conclusions=[c],
                           contradictions=[], insufficient_evidence=[],
                           created_at_epoch=1.0)


class DecisionConfidenceNotAuthorityTests(unittest.TestCase):
    def test_high_confidence_still_blocked_by_policy(self):
        ro = _ro(conf=0.95)
        # High-risk candidate blocked even at 0.95 confidence.
        candidates = [
            {"strategy_id": "st_del", "name": "del", "problem_class": "bug_fix",
             "tool_sequence": ["file.delete"], "confidence": 0.95},
        ]
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertIsNone(d.selected_strategy_id)
        self.assertEqual(d.status, "needs_guidance")

    def test_high_confidence_does_not_auto_approve_medium(self):
        ro = _ro(conf=0.99)
        candidates = [
            {"strategy_id": "st_w", "name": "write", "problem_class": "bug_fix",
             "tool_sequence": ["file.write"], "confidence": 0.99},
        ]
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        # Selected but still requires approval.
        self.assertEqual(d.status, "selected")
        self.assertTrue(d.approval_required)

    def test_custom_policy_not_bypassed_by_confidence(self):
        ro = _ro(conf=0.99)
        policy = Policy(policy_id="block_all", domain="decide",
                        when={"task_type": "bug_fix"},
                        effect={"blocked": True})
        engine = PolicyEngine(policies=[policy])
        candidates = [
            {"strategy_id": "st_a", "name": "a", "problem_class": "bug_fix",
             "tool_sequence": ["file.read"], "confidence": 0.99},
        ]
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context={"context_id": "ctx_a"}, policy_engine=engine,
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertIsNone(d.selected_strategy_id)
        self.assertEqual(d.status, "needs_guidance")

    def test_policy_is_authoritative_over_scoring(self):
        # A low-confidence safe candidate is selected over a high-confidence
        # blocked candidate.
        ro = _ro(conf=0.81)
        candidates = [
            {"strategy_id": "st_safe", "name": "safe",
             "problem_class": "bug_fix", "tool_sequence": ["file.read"],
             "confidence": 0.5},
            {"strategy_id": "st_del", "name": "del",
             "problem_class": "bug_fix", "tool_sequence": ["file.delete"],
             "confidence": 0.99},
        ]
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertEqual(d.selected_strategy_id, "st_safe")
