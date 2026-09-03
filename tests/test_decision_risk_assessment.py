"""Phase 7: risk levels correctly assigned."""

import unittest

from intelligence.decision import decide, DecisionStore
from intelligence.decision.types import RiskLevel
from intelligence.reasoning.evidence_chain import build_chain
from intelligence.reasoning.types import Conclusion, EvidenceStep, ReasoningQuery
from intelligence.reasoning.schema import ReasoningOutput


def _ro(ctx_id="ctx_a", conf=0.81):
    chain = build_chain([EvidenceStep("k1", "knowledge", "x", 0.9)])
    c = Conclusion(claim="x", confidence=conf, evidence_chain=chain,
                   context_id=ctx_id, source="knowledge")
    q = ReasoningQuery(question="q", task_type="bug_fix", domain="lint",
                       target={})
    return ReasoningOutput(reasoning_id="rs_a", query=q.to_dict(),
                           context_id=ctx_id, conclusions=[c],
                           contradictions=[], insufficient_evidence=[],
                           created_at_epoch=1.0)


class DecisionRiskAssessmentTests(unittest.TestCase):
    def test_read_only_is_low_risk(self):
        ro = _ro()
        candidates = [
            {"strategy_id": "st_r", "name": "read", "problem_class": "generic",
             "tool_sequence": ["file.read", "file.diff"], "confidence": 0.8},
        ]
        d = decide("t1", "generic", candidates, reasoning_output=ro,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertEqual(d.risk_level, RiskLevel.LOW)
        self.assertFalse(d.approval_required)
        self.assertEqual(d.kind, "autonomous")

    def test_reversible_write_is_medium_risk_requires_approval(self):
        ro = _ro()
        candidates = [
            {"strategy_id": "st_w", "name": "write", "problem_class": "bug_fix",
             "tool_sequence": ["file.write", "project.test"], "confidence": 0.8},
        ]
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertEqual(d.risk_level, RiskLevel.MEDIUM)
        self.assertTrue(d.approval_required)
        self.assertEqual(d.kind, "proposed")

    def test_irreversible_is_high_risk_blocked(self):
        ro = _ro()
        candidates = [
            {"strategy_id": "st_del", "name": "del", "problem_class": "bug_fix",
             "tool_sequence": ["file.delete", "file.remove"], "confidence": 0.9},
        ]
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context={"context_id": "ctx_a"},
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        # high risk blocked by default policy -> guidance
        self.assertEqual(d.status, "needs_guidance")
        self.assertTrue(d.approval_required)
        self.assertEqual(d.risk_level, RiskLevel.HIGH)

    def test_risk_affects_scoring(self):
        # Medium risk has a 0.75 discount vs low.
        from intelligence.decision.scorer import effective_score
        low = effective_score(0.8, 1.0, 1.0, 1.0, RiskLevel.LOW)
        med = effective_score(0.8, 1.0, 1.0, 1.0, RiskLevel.MEDIUM)
        self.assertGreater(low, med)
