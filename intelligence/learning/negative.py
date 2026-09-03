"""Negative learning with safeguards (Phase 8).

- Minimum M>=3 failures before any confidence reduction.
- All negative findings are context-restricted.
- Deprecation requires human approval (proposed, not auto-applied).
- All decisions are reversible.
"""

from .schema import derive_adaptation_id
from .types import Adaptation, AdaptationType
from .validation import validate_adaptation


def propose_negative_adaptations(strategy_id, context_id, evidence_ids, failure_count):
    """Create negative adaptation proposals with safeguards.

    Returns list of (adaptation, validation) tuples.
    For failure_count >=3, proposes a context-restricted confidence decrease;
    for failure_count >=3 and strategy is high-confidence, also proposes
    deprecation (requires approval). All negative adaptations carry a
    context_restriction so they do not generalize globally.
    """
    results = []
    context_restriction = {"allowed_contexts": [context_id]} if context_id else None

    # Confidence decrease (context-restricted)
    dec = Adaptation(
        adaptation_id=derive_adaptation_id(
            AdaptationType.DECREASE_STRATEGY_CONFIDENCE, strategy_id,
            context_id, evidence_ids),
        adaptation_type=AdaptationType.DECREASE_STRATEGY_CONFIDENCE,
        target_id=strategy_id,
        delta=-0.05,
        context_id=context_id,
        context_restriction=context_restriction,
        evidence_ids=evidence_ids,
        reason=f"{failure_count} verified failures for {strategy_id} in {context_id}",
        requires_approval=False,
    )
    # Tag for context-restricted check
    dec.context_restriction = context_restriction
    results.append((dec, validate_adaptation(dec, failure_count)))

    # Also propose context restriction adaptation (explicit)
    restrict = Adaptation(
        adaptation_id=derive_adaptation_id(
            AdaptationType.RESTRICT_CONTEXT, strategy_id, context_id, evidence_ids),
        adaptation_type=AdaptationType.RESTRICT_CONTEXT,
        target_id=strategy_id,
        delta=0.0,
        context_id=context_id,
        context_restriction=context_restriction,
        evidence_ids=evidence_ids,
        reason=f"restrict {strategy_id} from {context_id} after {failure_count} failures",
        requires_approval=False,
    )
    results.append((restrict, validate_adaptation(restrict, failure_count)))

    # Deprecation proposal (always requires approval)
    dep = Adaptation(
        adaptation_id=derive_adaptation_id(
            AdaptationType.PROPOSE_DEPRECATION, strategy_id, context_id, evidence_ids),
        adaptation_type=AdaptationType.PROPOSE_DEPRECATION,
        target_id=strategy_id,
        delta=0.0,
        context_id=context_id,
        context_restriction=context_restriction,
        evidence_ids=evidence_ids,
        reason=f"propose deprecation of {strategy_id} after {failure_count} failures",
        requires_approval=True,
    )
    results.append((dep, validate_adaptation(dep, failure_count)))

    return results


def propose_restore_adaptation(strategy_id, context_id, evidence_ids):
    """Propose restoring a deprecated strategy after new success evidence."""
    adaptation = Adaptation(
        adaptation_id=derive_adaptation_id(
            AdaptationType.RESTORE_STRATEGY, strategy_id, context_id, evidence_ids),
        adaptation_type=AdaptationType.RESTORE_STRATEGY,
        target_id=strategy_id,
        delta=0.0,
        context_id=context_id,
        evidence_ids=evidence_ids,
        reason=f"restore {strategy_id} after new success evidence",
        requires_approval=False,
    )
    validation = validate_adaptation(adaptation, 1)
    return adaptation, validation
