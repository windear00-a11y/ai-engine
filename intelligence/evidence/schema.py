"""EvidenceRecord data model (Phase 2).

An evidence record is an immutable, deterministic statement of a verified
observation. ``evidence_id`` is a content hash so that the same inputs always
produce the same id (deterministic and idempotent).
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional

from intelligence.evidence.types import EvidenceType


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def derive_evidence_id(source_observation_id, claim, context_id,
                       evidence_type, supporting_data) -> str:
    payload = {
        "source_observation_id": source_observation_id,
        "claim": claim,
        "context_id": context_id,
        "evidence_type": evidence_type.value
        if isinstance(evidence_type, EvidenceType) else str(evidence_type),
        "supporting_data": supporting_data or {},
    }
    return "ev_" + hashlib.sha256(
        _canonical(payload).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class EvidenceRecord:
    """An immutable verified observation."""
    evidence_id: str
    source_observation_id: str
    claim: str
    context_id: Optional[str]
    evidence_type: EvidenceType
    supporting_data: dict = field(default_factory=dict)
    created_at_epoch: float = 0.0

    def as_dict(self):
        return {
            "evidence_id": self.evidence_id,
            "source_observation_id": self.source_observation_id,
            "claim": self.claim,
            "context_id": self.context_id,
            "evidence_type": self.evidence_type.value,
            "supporting_data": self.supporting_data,
            "created_at_epoch": self.created_at_epoch,
        }

    def to_json(self):
        return json.dumps(self.as_dict(), sort_keys=True, default=str)
