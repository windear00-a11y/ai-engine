"""Positive learning: reinforce successful strategies (Phase 8)."""

from .schema import derive_adaptation_id
from .types import Adaptation, AdaptationType
from .validation import validate_adaptation


def propose_positive_adaptation(strategy_id, context_id, evidence_ids, success_count):
    """Create and validate a positive adaptation proposal.

    Returns (adaptation, validation).
    success_count includes the current success plus prior verified successes.
    """
    adaptation = Adaptation(
        adaptation_id=derive_adaptation_id(
            AdaptationType.INCREASE_STRATEGY_CONFIDENCE, strategy_id,
            context_id, evidence_ids),
        adaptation_type=AdaptationType.INCREASE_STRATEGY_CONFIDENCE,
        target_id=strategy_id,
        delta=0.05,
        context_id=context_id,
        evidence_ids=evidence_ids,
        reason=f"{success_count} verified successes for {strategy_id}",
        requires_approval=False,
    )
    validation = validate_adaptation(adaptation, success_count)
    return adaptation, validation
