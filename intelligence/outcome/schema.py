"""Outcome data model (Phase 2).

An Outcome is a verified classification of an execution result. It carries
the ids of the verification evidence it is based on, so every outcome is
auditable back to the evidence that supports its classification.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from intelligence.outcome.types import OutcomeClassification


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def derive_outcome_id(plan_id, context_id, classification,
                      verification_evidence_ids, metadata) -> str:
    payload = {
        "plan_id": plan_id,
        "context_id": context_id,
        "classification": classification.value
        if isinstance(classification, OutcomeClassification)
        else str(classification),
        "verification_evidence_ids": sorted(verification_evidence_ids or []),
        "metadata": metadata or {},
    }
    return "oc_" + hashlib.sha256(
        _canonical(payload).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class Outcome:
    """A verified classification of an execution result."""
    outcome_id: str
    plan_id: str
    context_id: Optional[str]
    classification: OutcomeClassification
    verification_evidence_ids: tuple = ()
    metadata: dict = field(default_factory=dict)
    created_at_epoch: float = 0.0

    def as_dict(self):
        return {
            "outcome_id": self.outcome_id,
            "plan_id": self.plan_id,
            "context_id": self.context_id,
            "classification": self.classification.value,
            "verification_evidence_ids": list(self.verification_evidence_ids),
            "metadata": self.metadata,
            "created_at_epoch": self.created_at_epoch,
        }

    def to_json(self):
        return json.dumps(self.as_dict(), sort_keys=True, default=str)
