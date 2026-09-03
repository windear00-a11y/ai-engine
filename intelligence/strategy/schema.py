"""Strategy data model (Phase 4).

A :class:`Strategy` is a stored, mutable-in-confidence object. Unlike evidence
and experience (append-only), a strategy's ``confidence``, ``superseded_by`` and
``deprecated`` flags may change -- but every such change must be audited (see
``StrategyStore`` and the append-only ``strategy_audit`` table).
"""

import hashlib
import json


def derive_strategy_id(problem_class, name):
    """Deterministic storage id for a strategy.

    Same (problem_class, name) always yields the same id (idempotent
    registration / seeding). Prefix ``st_`` + 32 hex chars.
    """
    canonical = json.dumps(
        {"problem_class": problem_class, "name": name},
        sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return "st_" + digest


class Strategy:
    """A reusable approach to a problem class."""

    __slots__ = (
        "strategy_id",
        "name",
        "description",
        "problem_class",
        "tool_sequence",
        "constraints",
        "confidence",
        "context_restrictions",
        "strategy_type",
        "superseded_by",
        "deprecated",
        "created_at_epoch",
        "updated_at_epoch",
    )

    def __init__(self, strategy_id, name, description, problem_class,
                 tool_sequence, constraints=None, confidence=0.0,
                 context_restrictions=None, strategy_type="explicit",
                 superseded_by=None, deprecated=False,
                 created_at_epoch=0.0, updated_at_epoch=0.0):
        self.strategy_id = strategy_id
        self.name = name
        self.description = description
        self.problem_class = problem_class
        self.tool_sequence = tuple(tool_sequence or ())
        self.constraints = dict(constraints or {})
        self.confidence = float(confidence)
        self.context_restrictions = dict(context_restrictions or {})
        self.strategy_type = strategy_type
        self.superseded_by = superseded_by
        self.deprecated = bool(deprecated)
        self.created_at_epoch = float(created_at_epoch)
        self.updated_at_epoch = float(updated_at_epoch)

    def to_row(self):
        return (
            self.strategy_id,
            self.name,
            self.description,
            self.problem_class,
            json.dumps(self.tool_sequence, separators=(",", ":")),
            json.dumps(self.constraints, sort_keys=True, default=str),
            self.confidence,
            json.dumps(self.context_restrictions,
                       sort_keys=True, default=str),
            self.strategy_type,
            self.superseded_by,
            1 if self.deprecated else 0,
            self.created_at_epoch,
            self.updated_at_epoch,
        )

    @classmethod
    def from_row(cls, row):
        return cls(
            strategy_id=row["strategy_id"],
            name=row["name"],
            description=row["description"],
            problem_class=row["problem_class"],
            tool_sequence=json.loads(row["tool_sequence_json"]),
            constraints=json.loads(row["constraints_json"] or "{}"),
            confidence=row["confidence"],
            context_restrictions=json.loads(
                row["context_restrictions_json"] or "{}"),
            strategy_type=row["strategy_type"],
            superseded_by=row["superseded_by"],
            deprecated=bool(row["deprecated"]),
            created_at_epoch=row["created_at_epoch"],
            updated_at_epoch=row["updated_at_epoch"],
        )

    def to_dict(self):
        return {
            "strategy_id": self.strategy_id,
            "name": self.name,
            "description": self.description,
            "problem_class": self.problem_class,
            "tool_sequence": list(self.tool_sequence),
            "constraints": self.constraints,
            "confidence": self.confidence,
            "context_restrictions": self.context_restrictions,
            "strategy_type": self.strategy_type,
            "superseded_by": self.superseded_by,
            "deprecated": self.deprecated,
            "created_at_epoch": self.created_at_epoch,
            "updated_at_epoch": self.updated_at_epoch,
        }
