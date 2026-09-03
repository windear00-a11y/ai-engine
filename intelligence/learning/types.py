"""Learning type definitions (Phase 8)."""

from enum import Enum


class AdaptationType(str, Enum):
    INCREASE_STRATEGY_CONFIDENCE = "increase_strategy_confidence"
    DECREASE_STRATEGY_CONFIDENCE = "decrease_strategy_confidence"
    RESTRICT_CONTEXT = "restrict_context"
    PROPOSE_DEPRECATION = "propose_deprecation"
    RESTORE_STRATEGY = "restore_strategy"


class Adaptation:
    """A single proposed adaptation from learning."""

    __slots__ = (
        "adaptation_id", "adaptation_type", "target_id",
        "delta", "context_id", "context_restriction",
        "evidence_ids", "reason", "requires_approval",
    )

    def __init__(self, adaptation_id, adaptation_type, target_id,
                 delta=0.0, context_id=None, context_restriction=None,
                 evidence_ids=None, reason="", requires_approval=False):
        self.adaptation_id = adaptation_id
        self.adaptation_type = adaptation_type if isinstance(
            adaptation_type, AdaptationType) else AdaptationType(adaptation_type)
        self.target_id = target_id
        self.delta = float(delta)
        self.context_id = context_id
        self.context_restriction = dict(context_restriction) if context_restriction else None
        self.evidence_ids = tuple(evidence_ids or [])
        self.reason = reason
        self.requires_approval = bool(requires_approval)

    def to_dict(self):
        return {
            "adaptation_id": self.adaptation_id,
            "adaptation_type": self.adaptation_type.value,
            "target_id": self.target_id,
            "delta": self.delta,
            "context_id": self.context_id,
            "context_restriction": self.context_restriction,
            "evidence_ids": list(self.evidence_ids),
            "reason": self.reason,
            "requires_approval": self.requires_approval,
        }


class LearningEvent:
    """Immutable record of what learning proposed/applied/rejected."""

    __slots__ = (
        "learning_event_id", "outcome_id", "context_id",
        "pattern_detected", "adaptations_proposed",
        "adaptations_applied", "adaptations_rejected",
        "evidence_chain", "created_at_epoch",
    )

    def __init__(self, learning_event_id, outcome_id, context_id,
                 pattern_detected, adaptations_proposed, adaptations_applied,
                 adaptations_rejected, evidence_chain, created_at_epoch=0.0):
        self.learning_event_id = learning_event_id
        self.outcome_id = outcome_id
        self.context_id = context_id
        self.pattern_detected = pattern_detected
        self.adaptations_proposed = list(adaptations_proposed)
        self.adaptations_applied = list(adaptations_applied)
        self.adaptations_rejected = list(adaptations_rejected)
        self.evidence_chain = dict(evidence_chain or {})
        self.created_at_epoch = float(created_at_epoch)

    def to_dict(self):
        return {
            "learning_event_id": self.learning_event_id,
            "outcome_id": self.outcome_id,
            "context_id": self.context_id,
            "pattern_detected": self.pattern_detected,
            "adaptations_proposed": [a.to_dict() if hasattr(a, "to_dict") else a
                                     for a in self.adaptations_proposed],
            "adaptations_applied": [a.to_dict() if hasattr(a, "to_dict") else a
                                    for a in self.adaptations_applied],
            "adaptations_rejected": [a.to_dict() if hasattr(a, "to_dict") else a
                                     for a in self.adaptations_rejected],
            "evidence_chain": self.evidence_chain,
            "created_at_epoch": self.created_at_epoch,
        }
