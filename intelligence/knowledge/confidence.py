"""Evidence-weighted confidence scoring for knowledge (Phase 5).

Confidence is a 0..1 score reflecting how well-supported a knowledge claim is.
Updates are automatic but audited: every change is recorded as a lifecycle
event linked to the evidence chain that justifies it.
"""

from .events import KnowledgeLifecycleEvent, derive_event_id
from .lifecycle import _get_lifecycle


def _read_confidence(node):
    lifecycle = _get_lifecycle(node)
    raw = lifecycle.get("confidence")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, value))


def base_confidence(node):
    """Deterministic base confidence from supported support counts.

    Conservative formula: confidence approaches 1 with corroborating support
    and is discounted by explicit contradictions:

        support   = number of distinct supporting observations
        contradict = number of contradictory observations
        conf = 1 / (1 + contradict) * (1 - 0.5 ** support)
    """
    if not isinstance(node, dict):
        node = {}
    support = 0
    contradict = 0
    for key, val in node.items():
        if "support" in key.lower():
            try:
                support += int(val)
            except (TypeError, ValueError):
                pass
        if "contradict" in key.lower():
            try:
                contradict += int(val)
            except (TypeError, ValueError):
                pass
    if support < 0:
        support = 0
    if contradict < 0:
        contradict = 0
    return round((1.0 / (1.0 + contradict)) * (1.0 - 0.5 ** support), 6)


def get_knowledge_confidence(node):
    """Return the stored confidence for a knowledge node (0 if none stored)."""
    return _read_confidence(node)


def _merge_confidence(repo, knowledge_id, new_confidence, created_at_epoch):
    from .lifecycle import _merge_lifecycle
    history = _get_lifecycle(repo.get_node(knowledge_id)).get(
        "confidence_history", [])
    _merge_lifecycle(repo, knowledge_id,
                     {"confidence": new_confidence,
                      "confidence_history": history + [{
                          "confidence": new_confidence,
                          "at_epoch": created_at_epoch,
                      }]},
                     "confidence update")
    return _read_confidence(repo.get_node(knowledge_id))


def update_knowledge_confidence(node, new_confidence, evidence_ids, repo,
                                event_store, created_at_epoch=0.0, note=None):
    """Update a knowledge node's confidence and audit the change.

    ``node`` is the pre-update node dict; ``repo`` is the repository the node
    came from. Returns the recorded lifecycle event. Raises KeyError if the
    node cannot be relocated (already modified/missing).
    """
    if new_confidence is None:
        new_confidence = base_confidence(node)
    new_confidence = round(max(0.0, min(1.0, float(new_confidence))), 6)
    old_confidence = _read_confidence(node)
    stored = _merge_confidence(repo, node.get("id"),
                               new_confidence, created_at_epoch)
    delta = round(new_confidence - old_confidence, 6)
    event_id = derive_event_id(node.get("id"), "confidence_update",
                               {"confidence": old_confidence},
                               {"confidence": stored, "delta": delta},
                               evidence_ids, created_at_epoch)
    event = KnowledgeLifecycleEvent(
        event_id=event_id, knowledge_id=node.get("id"),
        event_type="confidence_update",
        old_value={"confidence": old_confidence},
        new_value={"confidence": stored, "delta": delta},
        evidence_ids=evidence_ids, status="applied",
        note=note or "confidence update (automatic, audited)",
        created_at_epoch=created_at_epoch)
    event_store.save(event)
    return event
