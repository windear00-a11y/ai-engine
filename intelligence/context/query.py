"""Context retrieval and comparison queries (Phase 1).

Provides :func:`get_context` to fetch a stored snapshot by id, and
:func:`similar_contexts` to rank stored snapshots by weighted similarity to a
query snapshot. Both are deterministic and read-only.
"""

from dataclasses import dataclass

from intelligence.context import store as _store
from intelligence.context.diff import context_similarity
from intelligence.context.schema import ContextSnapshot


@dataclass(frozen=True)
class ContextSimilarity:
    context_id: str
    similarity: float
    snapshot: ContextSnapshot


def get_context(context_id, store=None):
    """Return the stored :class:`ContextSnapshot` for ``context_id`` or None."""
    ctx_store = store or _store.ContextStore()
    try:
        return ctx_store.get(context_id)
    finally:
        if store is None:
            ctx_store.close()


def similar_contexts(snapshot, limit=10, store=None, min_similarity=0.0):
    """Rank stored snapshots by similarity to ``snapshot``.

    Returns a list of :class:`ContextSimilarity` (highest first). ``limit``
    caps the number returned. Deterministic (stable order by similarity then
    context_id).
    """
    ctx_store = store or _store.ContextStore()
    try:
        results = []
        for stored in ctx_store.all():
            sim = context_similarity(snapshot, stored)
            if sim >= min_similarity:
                results.append(ContextSimilarity(
                    context_id=stored.context_id,
                    similarity=sim,
                    snapshot=stored,
                ))
        results.sort(key=lambda r: (-r.similarity, r.context_id))
        return results[:limit]
    finally:
        if store is None:
            ctx_store.close()
