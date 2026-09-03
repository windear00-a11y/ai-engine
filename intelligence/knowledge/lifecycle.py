"""Knowledge lifecycle: supersede, invalidate, context-restrict (Phase 5).

Superseding and invalidation are *high-authority* operations. Following the
safety/authority invariant, the lifecycle layer only *proposes* these changes;
the actual mutation of the knowledge node is deferred until explicit approval
passes the existing ApprovalGate. Confidence updates are automatic but audited;
context restriction is narrow, reversible, and audited directly.

Lifecycle metadata is written into the existing ``nodes.metadata`` JSON column
through :class:`KnowledgeRepository` (the only module that touches knowledge.db)
-- the knowledge.db schema is never modified. Every lifecycle change is also
recorded as an immutable event in ``database/evidence.db``.
"""

from .events import (
    KnowledgeLifecycleEvent,
    LifecycleEventStore,
    derive_event_id,
)

# Keys written into a node's metadata JSON under a ``lifecycle`` sub-object.
_LIFECYCLE_KEY = "lifecycle"

# Structural keys added by KnowledgeRepository._node_dict on top of the node's
# metadata JSON. When reconstructing the stored metadata dict from a flattened
# node, these are excluded.
_STRUCTURAL_KEYS = frozenset(
    {"id", "type", "name", "description", "source_id", "provenance",
     "relationships"})


def _get_lifecycle(node):
    if not isinstance(node, dict):
        return {}
    lifecycle = node.get(_LIFECYCLE_KEY)
    if not isinstance(lifecycle, dict):
        return {}
    return lifecycle


def _stored_metadata(node):
    """Reconstruct the metadata JSON dict from a flattened node dict.

    KnowledgeRepository flattens stored metadata keys into the node dict.
    Everything except the structural keys is the stored metadata.
    """
    if not isinstance(node, dict):
        return {}
    return {k: v for k, v in node.items()
            if k not in _STRUCTURAL_KEYS and k != _LIFECYCLE_KEY}


def _merge_lifecycle(repo, knowledge_id, changes, note):
    """Merge *changes* into the node's lifecycle metadata via the repository.

    Returns the node dict (with relationships) after the merge, or None if the
    node does not exist.
    """
    node = repo.get_node(knowledge_id)
    if node is None:
        return None
    metadata = _stored_metadata(node)
    lifecycle = dict(_get_lifecycle(node))
    lifecycle.update(changes)
    metadata[_LIFECYCLE_KEY] = lifecycle
    ok = repo.update_node_metadata(knowledge_id, metadata)
    if not ok:
        return None
    after = repo.get_node(knowledge_id)
    return ({"metadata": after.get(_LIFECYCLE_KEY), "id": after.get("id")}
            if after is not None else None)


def _record(repo, event_store, knowledge_id, event_type, old_value,
            new_value, evidence_ids, status, created_at_epoch, note):
    event_id = derive_event_id(knowledge_id, event_type, old_value,
                               new_value, evidence_ids, created_at_epoch)
    event = KnowledgeLifecycleEvent(
        event_id=event_id, knowledge_id=knowledge_id, event_type=event_type,
        old_value=old_value, new_value=new_value, evidence_ids=evidence_ids,
        status=status, note=note, created_at_epoch=created_at_epoch)
    event_store.save(event)
    return event


# ---- supersede (propose -> approve) ----

def propose_supersede_knowledge(old_id, new_id, evidence_ids, event_store,
                                created_at_epoch=0.0, note=None):
    """Propose replacing ``old_id`` with ``new_id``. Does NOT mutate nodes.

    Records a pending supersede event only; the actual metadata change is
    deferred until :func:`approve_supersede_knowledge` passes the gate.
    """
    return _record(None, event_store, old_id, "supersede",
                   {"status": "active", "superseded_by": None},
                   {"status": "pending_superseded", "superseded_by": new_id},
                   evidence_ids, "pending", created_at_epoch,
                   note or "proposed supersede (pending approval) -- "
                           "not auto-applied")


def approve_supersede_knowledge(old_id, new_id, evidence_ids, repo,
                                event_store, created_at_epoch=0.0,
                                note=None):
    """Apply a supersede after approval: mark ``old_id`` superseded by
    ``new_id``. Audited. Raises KeyError if either node is missing."""
    old_node = repo.get_node(old_id)
    if old_node is None:
        raise KeyError(old_id)
    new_node = repo.get_node(new_id)
    if new_node is None:
        raise KeyError(new_id)
    old_life = _get_lifecycle(old_node)
    new_life = _get_lifecycle(new_node)
    _merge_lifecycle(repo, old_id,
                     {"status": "superseded",
                      "superseded_by": new_id,
                      "superseded_at_epoch": created_at_epoch},
                     note or "supersede approved")
    _merge_lifecycle(repo, new_id,
                     {"status": "active",
                      "supersedes": old_id,
                      "superseded_at_epoch": created_at_epoch},
                     note or "becomes active via supersede approved")
    record = _record(repo, event_store, old_id, "supersede",
                     {"status": old_life.get("status", "active"),
                      "superseded_by": old_life.get("superseded_by")},
                     {"status": "superseded", "superseded_by": new_id},
                     evidence_ids, "applied", created_at_epoch,
                     note or "supersede applied")
    return record


# ---- invalidate (propose -> approve) ----

def propose_invalidate_knowledge(knowledge_id, reason, evidence_ids,
                                 event_store, created_at_epoch=0.0, note=None):
    """Propose invalidating a node. Does NOT mutate it; records a pending
    event until :func:`approve_invalidate_knowledge` passes the gate."""
    return _record(None, event_store, knowledge_id, "invalidate",
                   {"status": "active", "reason": None},
                   {"status": "pending_invalidated", "reason": reason},
                   evidence_ids, "pending", created_at_epoch,
                   note or "proposed invalidation (pending approval) -- "
                           "not auto-applied")


def approve_invalidate_knowledge(knowledge_id, reason, evidence_ids, repo,
                                 event_store, created_at_epoch=0.0, note=None):
    """Apply invalidation after approval: mark the node invalidated. Audited.
    Raises KeyError if the node is missing."""
    node = repo.get_node(knowledge_id)
    if node is None:
        raise KeyError(knowledge_id)
    old_life = _get_lifecycle(node)
    _merge_lifecycle(repo, knowledge_id,
                     {"status": "invalidated", "reason": reason,
                      "invalidated_at_epoch": created_at_epoch},
                     note or "invalidation approved")
    record = _record(repo, event_store, knowledge_id, "invalidate",
                     {"status": old_life.get("status", "active"),
                      "reason": old_life.get("reason")},
                     {"status": "invalidated", "reason": reason},
                     evidence_ids, "applied", created_at_epoch,
                     note or "invalidation applied")
    return record


# ---- context restriction (direct, audited) ----

def restrict_context(knowledge_id, context_restrictions, repo, event_store,
                     created_at_epoch=0.0, note=None):
    """Restrict a knowledge node to specific contexts. Audited directly
    (narrow, reversible change; not classified as high-authority). Raises
    KeyError if the node is missing."""
    if repo.get_node(knowledge_id) is None:
        raise KeyError(knowledge_id)
    before = _get_lifecycle(repo.get_node(knowledge_id)).get(
        "context_restrictions", {})
    _merge_lifecycle(repo, knowledge_id,
                     {"context_restrictions": dict(context_restrictions or {})},
                     note or "context restriction")
    record = _record(repo, event_store, knowledge_id, "restrict_context",
                     {"context_restrictions": before},
                     {"context_restrictions": dict(context_restrictions or {})},
                     [], "applied", created_at_epoch,
                     note or "context restriction applied")
    return record
