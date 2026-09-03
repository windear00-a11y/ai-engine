"""Validate adaptations against policy thresholds (Phase 8)."""

from .types import AdaptationType

# Thresholds per Decision 3: knowledge promotion N>=3, strategy increase M>=2,
# deprecation M>=3, negative learning minimum 3.
# We expose them as constants so tests can reason deterministically.
MIN_SUCCESS_FOR_INCREASE = 2
MIN_FAILURE_FOR_DECREASE = 3
MIN_FAILURE_FOR_DEPRECATION = 3


class AdaptationValidation:
    __slots__ = ("adaptation", "approved", "reason")

    def __init__(self, adaptation, approved, reason=""):
        self.adaptation = adaptation
        self.approved = bool(approved)
        self.reason = reason

    def to_dict(self):
        return {
            "adaptation_id": self.adaptation.adaptation_id if self.adaptation else None,
            "approved": self.approved,
            "reason": self.reason,
        }


def validate_adaptation(adaptation, evidence_count, context_match=None):
    """Deterministic validation of a single adaptation.

    evidence_count is the number of independent confirmations for the pattern.
    Returns AdaptationValidation.
    """
    atype = adaptation.adaptation_type
    # Resolve thresholds
    if atype == AdaptationType.INCREASE_STRATEGY_CONFIDENCE:
        if evidence_count >= MIN_SUCCESS_FOR_INCREASE:
            return AdaptationValidation(adaptation, True,
                                        f"meets threshold {MIN_SUCCESS_FOR_INCREASE}")
        return AdaptationValidation(adaptation, False,
                                    f"below threshold {MIN_SUCCESS_FOR_INCREASE}: {evidence_count}")
    if atype in (AdaptationType.DECREASE_STRATEGY_CONFIDENCE,
                 AdaptationType.RESTRICT_CONTEXT):
        if evidence_count >= MIN_FAILURE_FOR_DECREASE:
            return AdaptationValidation(adaptation, True,
                                        f"meets threshold {MIN_FAILURE_FOR_DECREASE}")
        return AdaptationValidation(adaptation, False,
                                    f"below threshold {MIN_FAILURE_FOR_DECREASE}: {evidence_count}")
    if atype == AdaptationType.PROPOSE_DEPRECATION:
        if evidence_count >= MIN_FAILURE_FOR_DEPRECATION:
            # Deprecation always requires human approval, but validation passes
            # the threshold check; approval is a separate gate.
            return AdaptationValidation(adaptation, True,
                                        f"meets threshold {MIN_FAILURE_FOR_DEPRECATION} (approval still required)")
        return AdaptationValidation(adaptation, False,
                                    f"below threshold {MIN_FAILURE_FOR_DEPRECATION}: {evidence_count}")
    if atype == AdaptationType.RESTORE_STRATEGY:
        # Restoration is always allowed (reversibility)
        return AdaptationValidation(adaptation, True, "restoration always allowed")
    return AdaptationValidation(adaptation, False, "unknown adaptation type")
