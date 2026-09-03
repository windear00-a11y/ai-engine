"""Phase 7: mutating actions require approval (D6, D7)."""

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


class DecisionHumanApprovalTests(unittest.TestCase):
    def test_low_risk_autonomous(self):
        ro = _ro()
        candidates = [
            {"strategy_id": "st_r", "name": "read", "problem_class": "generic",
             "tool_sequence": ["file.read", "file.diff"], "confidence": 0.8},
        ]
        d = decide("t1", "generic", candidates, reasoning_output=ro,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertFalse(d.approval_required)
        self.assertEqual(d.kind, "autonomous")
        self.assertEqual(d.status, "selected")

    def test_medium_risk_requires_approval(self):
        ro = _ro()
        candidates = [
            {"strategy_id": "st_w", "name": "write", "problem_class": "bug_fix",
             "tool_sequence": ["file.write", "project.test"], "confidence": 0.8},
        ]
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertTrue(d.approval_required)
        self.assertEqual(d.kind, "proposed")
        self.assertEqual(d.status, "selected")
        # Decision proposes; it does not execute. ApprovalGate decides.
        self.assertIsNotNone(d.selected_strategy_id)

    def test_high_risk_blocked_requires_guidance(self):
        ro = _ro()
        candidates = [
            {"strategy_id": "st_del", "name": "del", "problem_class": "bug_fix",
             "tool_sequence": ["file.delete"], "confidence": 0.9},
        ]
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertTrue(d.approval_required)
        self.assertEqual(d.status, "needs_guidance")

    def test_decision_does_not_bypass_approval(self):
        # Even a selected medium-risk decision still has approval_required.
        ro = _ro(conf=0.95)
        candidates = [
            {"strategy_id": "st_w", "name": "write", "problem_class": "bug_fix",
             "tool_sequence": ["file.write"], "confidence": 0.95},
        ]
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertEqual(d.status, "selected")
        self.assertTrue(d.approval_required)
