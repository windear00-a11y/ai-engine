"""Decision data model and deterministic id (Phase 7)."""

import hashlib
import json

from .types import Decision


def derive_decision_id(task_id, selected_strategy_id, reasoning_id,
                       context_id, confidence):
    """Deterministic decision id.

    Identical (task_id, selected_strategy, reasoning_id, context_id,
    confidence) always yield the same id, so decision-making is reproducible
    for identical inputs. Prefix ``ds_`` + 32 hex chars.
    """
    stream = {
        "task_id": task_id,
        "selected_strategy_id": selected_strategy_id,
        "reasoning_id": reasoning_id,
        "context_id": context_id,
        "confidence": confidence,
    }
    canonical = json.dumps(stream, sort_keys=True, separators=(",", ":"),
                           default=str)
    return "ds_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def decision_to_dict(decision):
    """Robust dict conversion for any Decision-shaped object."""
    if isinstance(decision, Decision):
        return decision.to_dict()
    if hasattr(decision, "to_dict"):
        return decision.to_dict()
    return decision
