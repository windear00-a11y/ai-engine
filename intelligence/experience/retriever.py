"""Experience retrieval (Phase 3).

Retrieval is deterministic. When a ``context_id`` is supplied for
``experience_for_task_type``, results are ordered by context similarity to the
referenced context's snapshot, so the most relevant experience ranks first.
"""

import os
from dataclasses import dataclass

from intelligence.context.diff import context_similarity
from intelligence.context.store import ContextStore
from intelligence.experience.store import ExperienceStore


def get_experience(experience_id, store=None):
    ctx_store = store or ExperienceStore()
    try:
        return ctx_store.get(experience_id)
    finally:
        if store is None:
            ctx_store.close()


def _context_snapshot_map(context_db_path):
    if not context_db_path or not os.path.isfile(context_db_path):
        return {}
    try:
        store = ContextStore(context_db_path)
        try:
            return {s.context_id: s for s in store.all()}
        finally:
            store.close()
    except Exception:
        return {}


def experience_for_task_type(task_type, context_id=None, limit=20,
                             store=None, context_db_path=None):
    """Return experiences filtered by task type.

    With ``context_id`` (and a resolvable ``context_db_path``), results are
    ranked by descending context similarity to that context's snapshot.
    Without it, results are returned in synthesis order.
    """
    ctx_store = store or ExperienceStore()
    try:
        matches = ctx_store.for_task_type(task_type)
    finally:
        if store is None:
            ctx_store.close()

    if context_id and context_db_path:
        snapshots = _context_snapshot_map(context_db_path)
        query_snap = snapshots.get(context_id)
        if query_snap is not None:
            def _key(rec):
                other = snapshots.get(rec.context_id)
                sim = context_similarity(query_snap, other) \
                    if other is not None else 0.0
                return (-sim, rec.synthesized_at_epoch, rec.experience_id)
            matches = sorted(matches, key=_key)
    return matches[:limit]


def experience_for_strategy(strategy_id, limit=100, store=None):
    ctx_store = store or ExperienceStore()
    try:
        return ctx_store.for_strategy(strategy_id)[:limit]
    finally:
        if store is None:
            ctx_store.close()


__all__ = [
    "get_experience",
    "experience_for_task_type",
    "experience_for_strategy",
]
