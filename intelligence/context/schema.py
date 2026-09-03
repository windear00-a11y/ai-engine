"""ContextSnapshot data model (Phase 1).

A snapshot is a point-in-time capture of the four context dimensions plus a
deterministic :attr:`context_id`.

Determinism of context_id
-------------------------
``context_id`` is a SHA-256 over the *canonical JSON* of the
system/project/task dimensions only. The temporal dimension is deliberately
excluded: two captures of the same environment must yield the SAME
context_id (idempotent), even though `captured_at_epoch` differs.
"""

import hashlib
import json
from dataclasses import dataclass, field


def _canonical(obj):
    """Deterministic canonical JSON string for hash inputs."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def derive_context_id(system: dict, project: dict, task: dict) -> str:
    """Deterministic context_id over the three similarity dimensions."""
    payload = {"system": system, "project": project, "task": task}
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return "ctx_" + digest[:32]


@dataclass(frozen=True)
class ContextSnapshot:
    """An immutable capture of operational context at a point in time."""
    system: dict = field(default_factory=dict)
    project: dict = field(default_factory=dict)
    task: dict = field(default_factory=dict)
    temporal: dict = field(default_factory=dict)
    context_id: str = ""
    captured_at_epoch: float = 0.0

    @classmethod
    def build(cls, system, project, task, temporal,
              captured_at_epoch=None):
        system = dict(system or {})
        project = dict(project or {})
        task = dict(task or {})
        context_id = derive_context_id(system, project, task)
        return cls(
            system=system,
            project=project,
            task=task,
            temporal=dict(temporal or {}),
            context_id=context_id,
            captured_at_epoch=captured_at_epoch,
        )

    def as_dict(self):
        return {
            "context_id": self.context_id,
            "captured_at_epoch": self.captured_at_epoch,
            "system": self.system,
            "project": self.project,
            "task": self.task,
            "temporal": self.temporal,
        }

    def to_json(self):
        return json.dumps(self.as_dict(), sort_keys=True, default=str)
