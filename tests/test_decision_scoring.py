"""Phase 7: scoring considers confidence, context match, evidence quality, risk."""

import unittest

from intelligence.decision.scorer import effective_score, raw_score
from intelligence.decision.types import RiskLevel
from intelligence.decision import decide, DecisionStore
from intelligence.reasoning.evidence_chain import build_chain
from intelligence.reasoning.types import Conclusion, EvidenceStep, ReasoningQuery
from intelligence.reasoning.schema import ReasoningOutput


def _ro_with_quality(chain_quality_target=0.95, ctx_id="ctx_a", conf=0.8):
    # Build chain with quality 0.95 (2 supports) vs 0.80 (1 support)
    if chain_quality_target >= 0.95:
        chain = build_chain([EvidenceStep("k1", "knowledge", "x", 0.9),
                             EvidenceStep("k2", "knowledge", "x", 0.85)])
    else:
        chain = build_chain([EvidenceStep("k1", "knowledge", "x", 0.9)])
    c = Conclusion(claim="x", confidence=conf, evidence_chain=chain,
                   context_id=ctx_id, source="knowledge")
    q = ReasoningQuery(question="q", task_type="bug_fix", domain="lint",
                       target={})
    return ReasoningOutput(reasoning_id="rs_a", query=q.to_dict(),
                           context_id=ctx_id, conclusions=[c],
                           contradictions=[], insufficient_evidence=[],
                           created_at_epoch=1.0)


class DecisionScoringTests(unittest.TestCase):
    def test_higher_confidence_higher_score(self):
        s_lo = effective_score(0.5, 1.0, 1.0, 1.0, RiskLevel.LOW)
        s_hi = effective_score(0.9, 1.0, 1.0, 1.0, RiskLevel.LOW)
        self.assertGreater(s_hi, s_lo)

    def test_better_context_match_higher_score(self):
        s_lo = effective_score(0.8, 0.5, 1.0, 1.0, RiskLevel.LOW)
        s_hi = effective_score(0.8, 1.0, 1.0, 1.0, RiskLevel.LOW)
        self.assertGreater(s_hi, s_lo)

    def test_evidence_quality_matters(self):
        s_lo = effective_score(0.8, 1.0, 0.5, 1.0, RiskLevel.LOW)
        s_hi = effective_score(0.8, 1.0, 1.0, 1.0, RiskLevel.LOW)
        self.assertGreater(s_hi, s_lo)

    def test_higher_risk_lower_effective_score(self):
        raw = raw_score(0.8, 1.0, 1.0, 1.0)
        eff_low = effective_score(0.8, 1.0, 1.0, 1.0, RiskLevel.LOW)
        eff_med = effective_score(0.8, 1.0, 1.0, 1.0, RiskLevel.MEDIUM)
        self.assertEqual(raw, eff_low)
        self.assertLess(eff_med, eff_low)

    def test_decide_selects_highest_scoring_candidate(self):
        # Two candidates, same risk, different confidences -> higher wins.
        ro = _ro_with_quality()
        candidates = [
            {"strategy_id": "st_lo", "name": "lo", "problem_class": "bug_fix",
             "tool_sequence": ["file.read"], "confidence": 0.4},
            {"strategy_id": "st_hi", "name": "hi", "problem_class": "bug_fix",
             "tool_sequence": ["file.read"], "confidence": 0.9},
        ]
        ctx = {"context_id": "ctx_a"}
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context=ctx, store=DecisionStore(":memory:"),
                   created_at_epoch=1.0)
        self.assertEqual(d.selected_strategy_id, "st_hi")
        # rationale contains score
        self.assertIn("score", d.rationale)

    def test_recency_weight_affects_score(self):
        s_old = effective_score(0.8, 1.0, 1.0, 0.5, RiskLevel.LOW)
        s_new = effective_score(0.8, 1.0, 1.0, 1.0, RiskLevel.LOW)
        self.assertGreater(s_new, s_old)
