"""Canonical Information / Knowledge / Experience lifecycle (Phase 26).

Exposes the lifecycle vocabulary (origins, roles, states, provenance),
deterministic provenance tracing (``intelligence.lifecycle.trace``), and the
lifecycle metadata store (``intelligence.lifecycle.store``).

Higher layers build on ``LifecycleRecordStore`` + the pure model; they never
need table names or raw SQL.
"""

from intelligence.lifecycle.model import (
    LifecycleRecord,
    LifecycleState,
    Origin,
    Provenance,
    RecordRole,
    RULE_EXTERNAL_NOT_EXPERIENCE,
    canonical_state_transition,
    check_role_origin,
    classify_origin,
    coerce_origin,
    coerce_role,
    coerce_state,
    confidence_label,
    derive_learning_record_id,
    derive_lifecycle_entry_id,
    role_compatible_origins,
)
from intelligence.lifecycle.store import LifecycleRecordStore
from intelligence.lifecycle.trace import trace_provenance

__all__ = [
    "LifecycleRecord",
    "LifecycleRecordStore",
    "LifecycleState",
    "Origin",
    "Provenance",
    "RecordRole",
    "RULE_EXTERNAL_NOT_EXPERIENCE",
    "canonical_state_transition",
    "check_role_origin",
    "classify_origin",
    "coerce_origin",
    "coerce_role",
    "coerce_state",
    "confidence_label",
    "derive_learning_record_id",
    "derive_lifecycle_entry_id",
    "role_compatible_origins",
    "trace_provenance",
]