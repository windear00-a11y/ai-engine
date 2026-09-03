"""Reasoning output data model (Phase 6)."""

import hashlib
import json


def derive_reasoning_id(query, context_id, conclusions, contradictions,
                        insufficient_evidence):
    """Deterministic reasoning id.

    Identical (query, context_id, conclusions, contradictions, flagged
    insufficiency) always yield the same id. Since outputs are a pure function
    of (query, context_id), this is reproducible for identical inputs.
    Prefix ``rs_`` + 32 hex chars.
    """
    def _conv(obj):
        if isinstance(obj, (ReasoningOutput,)):
            return obj.to_dict()
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        return obj

    stream = {
        "query": query,
        "context_id": context_id,
        "conclusions": [_conv(c) for c in conclusions],
        "contradictions": [_conv(c) for c in contradictions],
        "insufficient_evidence": [_conv(i) for i in insufficient_evidence],
    }
    canonical = json.dumps(stream, sort_keys=True, separators=(",", ":"),
                           default=_conv)
    return "rs_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class ReasoningOutput:
    """The full deterministic result of a reasoning pass."""

    __slots__ = ("reasoning_id", "query", "context_id", "conclusions",
                 "contradictions", "insufficient_evidence", "created_at_epoch")

    def __init__(self, reasoning_id, query, context_id, conclusions,
                 contradictions, insufficient_evidence, created_at_epoch=0.0):
        self.reasoning_id = reasoning_id
        self.query = query if isinstance(query, dict) else query.to_dict()
        self.context_id = context_id
        self.conclusions = list(conclusions)
        self.contradictions = list(contradictions)
        self.insufficient_evidence = list(insufficient_evidence)
        self.created_at_epoch = float(created_at_epoch)

    def to_dict(self):
        return {
            "reasoning_id": self.reasoning_id,
            "query": self.query,
            "context_id": self.context_id,
            "conclusions": [c.to_dict() for c in self.conclusions],
            "contradictions": [c.to_dict() for c in self.contradictions],
            "insufficient_evidence": [i.to_dict()
                                      for i in self.insufficient_evidence],
            "created_at_epoch": self.created_at_epoch,
        }
