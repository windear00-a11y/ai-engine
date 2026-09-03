"""Confidence-weighted scoring for the decision engine (Phase 7).

Implements the D-rules scoring model deterministically:

    raw_score = confidence * context_match * evidence_quality * recency_weight

Per D4, risk affects selection: high/medium risk candidates are not selected
autonomously (authority boundaries), and within an acceptable set the
highest raw score wins (D1). The effective score is the raw score, reduced by
a risk factor for medium/high risk so that risky options need higher
evidence to compete (D4 "high-risk candidates need higher confidence to be
selected").
"""

from .types import RiskLevel

# Risk discount: medium-risk raw score is reduced 25%; high-risk candidates
# are blocked entirely by policy, so they never reach selection.
_RISK_DISCOUNT = {
    RiskLevel.LOW: 1.0,
    RiskLevel.MEDIUM: 0.75,
    RiskLevel.HIGH: 0.0,
}


def raw_score(confidence, context_match=1.0, evidence_quality=1.0,
              recency_weight=1.0):
    """D3 score: confidence * context_match * evidence_quality * recency."""
    return round(
        float(confidence) * float(context_match)
        * float(evidence_quality) * float(recency_weight),
        6)


def effective_score(confidence, context_match=1.0, evidence_quality=1.0,
                    recency_weight=1.0, risk_level=RiskLevel.LOW):
    """D1/D4 effective score with risk discount applied."""
    base = raw_score(confidence, context_match, evidence_quality,
                     recency_weight)
    return round(base * _RISK_DISCOUNT.get(risk_level, 1.0), 6)


def score_candidate(strategy_confidence, context_match, evidence_quality,
                    recency_weight, risk_level):
    """Score a candidate, returning (raw_score, effective_score)."""
    return (
        raw_score(strategy_confidence, context_match, evidence_quality,
                  recency_weight),
        effective_score(strategy_confidence, context_match, evidence_quality,
                        recency_weight, risk_level),
    )
