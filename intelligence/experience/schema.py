"""ExperienceRecord data model (Phase 3).

An experience is an interpreted summary -- NOT the raw execution log. It
links a task to its context, outcome, and the evidence chain that supports
the outcome. `summary` holds the interpreted content (what was learned), and
deliberately excludes raw step inputs/results.

``experience_id`` is deterministic: the same (task_id, context_id, outcome_id,
evidence_ids, strategy_id) always yields the same id.
"""

import hashlib
import json
from dataclasses import dataclass, field
from typing import Optional


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def derive_experience_id(task_id, context_id, outcome_id, evidence_ids,
                         strategy_id) -> str:
    payload = {
        "task_id": task_id,
        "context_id": context_id,
        "outcome_id": outcome_id,
        "evidence_ids": sorted(evidence_ids or []),
        "strategy_id": strategy_id,
    }
    return "xp_" + hashlib.sha256(
        _canonical(payload).encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True)
class ExperienceRecord:
    """An immutable, interpreted summary of a task's execution experience."""
    experience_id: str
    task_id: str
    task_type: str
    domain: str
    context_id: str
    outcome_id: str
    evidence_ids: tuple = ()
    strategy_id: Optional[str] = None
    summary: dict = field(default_factory=dict)
    synthesized_at_epoch: float = 0.0

    def as_dict(self):
        return {
            "experience_id": self.experience_id,
            "task_id": self.task_id,
            "task_type": self.task_type,
            "domain": self.domain,
            "context_id": self.context_id,
            "outcome_id": self.outcome_id,
            "evidence_ids": list(self.evidence_ids),
            "strategy_id": self.strategy_id,
            "summary": self.summary,
            "synthesized_at_epoch": self.synthesized_at_epoch,
        }

    def to_json(self):
        return json.dumps(self.as_dict(), sort_keys=True, default=str)
