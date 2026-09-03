"""Context Capture (Phase 1).

Captures, stores, queries, and compares operational context snapshots. Context
is the foundation for applicability reasoning: without it the system cannot
know whether past experience applies to current conditions.

Public API
----------
:func:`capture_context`      Deterministically capture a snapshot.
:func:`get_context`          Fetch a stored snapshot by id.
:func:`similar_contexts`     Rank stored snapshots by similarity.
:func:`context_similarity`   Weighted similarity between two snapshots.
:func:`context_diff`         Which dimensions differ between two snapshots.
:class:`ContextSnapshot`     The immutable snapshot value type.
:class:`ContextStore`        Append-only SQLite persistence.
"""

from intelligence.context.capture import capture_context
from intelligence.context.diff import context_diff, context_similarity
from intelligence.context.query import (
    ContextSimilarity,
    get_context,
    similar_contexts,
)
from intelligence.context.schema import ContextSnapshot
from intelligence.context.store import ContextStore, default_context_db_path

__all__ = [
    "capture_context",
    "get_context",
    "similar_contexts",
    "context_similarity",
    "context_diff",
    "ContextSnapshot",
    "ContextSimilarity",
    "ContextStore",
    "default_context_db_path",
]
