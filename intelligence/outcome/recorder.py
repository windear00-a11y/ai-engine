"""Outcome recording from verification results (Phase 2).

An outcome may only be recorded when it is supported by at least one
verification evidence id. Recording without verification evidence raises
:class:`VerificationRequiredError`.
"""

from intelligence.outcome.schema import Outcome, derive_outcome_id
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification, classify_outcome


class VerificationRequiredError(ValueError):
    """Raised when an outcome would be recorded without verification evidence."""


def record_outcome(plan_id, context_id, classification,
                   verification_evidence_ids, metadata=None,
                   store=None, created_at_epoch=None):
    """Record a verified outcome.

    Parameters
    ----------
    classification : OutcomeClassification | str
    verification_evidence_ids : list[str]
        Must be non-empty (an outcome requires verification evidence).

    Raises
    ------
    VerificationRequiredError
        If ``verification_evidence_ids`` is empty.
    ValueError
        If ``classification`` is not a valid outcome classification.
    """
    import time
    if isinstance(classification, str):
        classification = OutcomeClassification(classification)
    if not isinstance(classification, OutcomeClassification):
        raise ValueError("classification must be an OutcomeClassification")

    evidence_ids = [str(e) for e in (verification_evidence_ids or [])]
    if not evidence_ids:
        raise VerificationRequiredError(
            "an outcome cannot be recorded without verification evidence; "
            "verification_evidence_ids is empty")
    if created_at_epoch is None:
        created_at_epoch = time.time()

    outcome_id = derive_outcome_id(
        plan_id, context_id, classification, evidence_ids, metadata)
    outcome = Outcome(
        outcome_id=outcome_id,
        plan_id=plan_id,
        context_id=context_id,
        classification=classification,
        verification_evidence_ids=tuple(evidence_ids),
        metadata=dict(metadata or {}),
        created_at_epoch=created_at_epoch,
    )
    ctx_store = store or OutcomeStore()
    try:
        ctx_store.save(outcome)
    finally:
        if store is None:
            ctx_store.close()
    return outcome


def get_outcome(outcome_id, store=None):
    ctx_store = store or OutcomeStore()
    try:
        return ctx_store.get(outcome_id)
    finally:
        if store is None:
            ctx_store.close()


def outcomes_for_plan(plan_id, store=None):
    ctx_store = store or OutcomeStore()
    try:
        return ctx_store.for_plan(plan_id)
    finally:
        if store is None:
            ctx_store.close()


__all__ = [
    "record_outcome",
    "get_outcome",
    "outcomes_for_plan",
    "classify_outcome",
    "VerificationRequiredError",
    "Outcome",
    "OutcomeClassification",
    "OutcomeStore",
]
