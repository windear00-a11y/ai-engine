"""Canonical information/knowledge/experience lifecycle model (Phase 26).

Single lifecycle grammar shared by every intelligence record::

    INFORMATION
        -> SOURCE / ORIGIN
        -> SOURCE RECORD
        -> STRUCTURED MEMORY
        -> KNOWLEDGE and/or EXPERIENCE
        -> LEARNING
        -> STRATEGY
        -> STRATEGY APPLICATION
        -> REASONING / DECISION
        -> PLAN
        -> AUTHORITY / APPROVAL
        -> ACTION
        -> OBSERVATION
        -> VERIFICATION
        -> OUTCOME
        -> EVIDENCE
        -> EXPERIENCE (loop)

This module is PURE: no database access, no network, no AI, no filesystem.
Every
function is deterministic in its inputs so the whole intelligence layer obeys
one lifecycle model regardless of the backing store.

Lifecycle concepts
------------------
* ``Origin`` — where a record came from (system_defined, user_provided,
  observed, external, derived).
* ``RecordRole`` — the role a record plays in the lifecycle (source_record,
  memory, knowledge, experience, outcome, evidence, learning, strategy,
  context).
* ``LifecycleState`` — the current lifecycle state of a record (active,
  superseded, deprecated, rejected, invalidated, archived). The state machine
  forbids impossible transitions, so a record can never silently skip from
  active straight to archived, for example.
* ``Provenance`` — the full provenance fingerprint attached to every record:
  who/what captured it, from where, when, in which project/context, what
  evidences support it, what it derives from, and its confidence.
* ``LifecycleRecord`` — one lifecycle-recorded entity with its Provenance.

Trust boundary rule (Rule 1)
----------------------------
EXTERNAL information is advisory: it is recorded as knowledge and grounded to
its source. It can NEVER become an EXPERIENCE directly — an experience is an
interpreted result of an action we (or a human) executed, and external claims
cannot be manufactured into experiences without execution. Enforcement lives
in :func:`check_role_origin`.
"""

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Set


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


# ---------------------------------------------------------------------------
# Enums — canonical lifecycle vocabulary
# ---------------------------------------------------------------------------


class Origin(str, Enum):
    """Where a lifecycle record originated."""

    SYSTEM_DEFINED = "system_defined"
    USER_PROVIDED = "user_provided"
    OBSERVED = "observed"
    EXTERNAL = "external"
    DERIVED = "derived"


class RecordRole(str, Enum):
    """The role a record plays in the lifecycle."""

    SOURCE_RECORD = "source_record"
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    EXPERIENCE = "experience"
    OUTCOME = "outcome"
    EVIDENCE = "evidence"
    LEARNING = "learning"
    STRATEGY = "strategy"
    CONTEXT = "context"
    STRATEGY_APPLICATION = "strategy_application"
    REASONING = "reasoning"
    DECISION = "decision"
    PLAN = "plan"
    AUTHORITY = "authority"
    AUTHORIZATION = "authorization"
    ACTION = "action"
    OBSERVATION = "observation"
    VERIFICATION = "verification"


class LifecycleState(str, Enum):
    """Lifecycle state of a record.

    Mirrors the existing conventions: knowledge nodes carry an active /
    pending_* / invalidated lifecycle, and strategies carry a deprecated /
    superseded flag — this enum unifies them under one vocabulary.
    """

    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DEPRECATED = "deprecated"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"
    ARCHIVED = "archived"


# Canonical (allowed) lifecycle state transitions. The state machine is
# conservative: states are only ever degraded under audit, never revived
# silently, and never skipped from active directly to archived.
_STATE_TRANSITIONS = {
    LifecycleState.ACTIVE: {
        LifecycleState.SUPERSEDED,
        LifecycleState.DEPRECATED,
        LifecycleState.REJECTED,
        LifecycleState.INVALIDATED,
    },
    LifecycleState.SUPERSEDED: {LifecycleState.ARCHIVED},
    LifecycleState.DEPRECATED: {LifecycleState.ARCHIVED},
    LifecycleState.REJECTED: {LifecycleState.ARCHIVED},
    LifecycleState.INVALIDATED: {LifecycleState.ARCHIVED},
    LifecycleState.ARCHIVED: set(),
}

# Which origins a given role may legitimately carry. EXPERIENCE deliberately
# excludes EXTERNAL (Rule 1: external claims never become experiences without
# execution) and SYSTEM_DEFINED (a system may hold knowledge, but an
# experience is an interpreted result of an action).
_ROLE_ORIGINS: dict = {
    RecordRole.SOURCE_RECORD: {
        Origin.SYSTEM_DEFINED,
        Origin.USER_PROVIDED,
        Origin.OBSERVED,
        Origin.EXTERNAL,
    },
    RecordRole.MEMORY: {
        Origin.SYSTEM_DEFINED,
        Origin.USER_PROVIDED,
        Origin.OBSERVED,
        Origin.EXTERNAL,
        Origin.DERIVED,
    },
    RecordRole.KNOWLEDGE: {
        Origin.SYSTEM_DEFINED,
        Origin.USER_PROVIDED,
        Origin.OBSERVED,
        Origin.EXTERNAL,
        Origin.DERIVED,
    },
    RecordRole.EXPERIENCE: {
        Origin.OBSERVED,
        Origin.USER_PROVIDED,
        Origin.DERIVED,
    },
    RecordRole.OUTCOME: {Origin.OBSERVED},
    RecordRole.EVIDENCE: {Origin.OBSERVED, Origin.USER_PROVIDED},
    RecordRole.LEARNING: {Origin.DERIVED},
    RecordRole.STRATEGY: {Origin.DERIVED, Origin.SYSTEM_DEFINED},
    RecordRole.CONTEXT: {
        Origin.SYSTEM_DEFINED,
        Origin.OBSERVED,
        Origin.USER_PROVIDED,
    },
    RecordRole.STRATEGY_APPLICATION: {Origin.DERIVED},
    RecordRole.REASONING: {Origin.DERIVED},
    RecordRole.DECISION: {Origin.DERIVED},
    RecordRole.PLAN: {Origin.DERIVED},
    # Phase 29 — Authority/approval boundary. AUTHORITY is a derived
    # evaluation; AUTHORIZATION is an explicit approval grant made by a
    # user/authorizer or conferred by the system; ACTION and OBSERVATION are
    # records of what was attempted / what happened (observed); VERIFICATION
    # is a derived evaluation. None of these carry EXTERNAL.
    RecordRole.AUTHORITY: {Origin.DERIVED},
    RecordRole.AUTHORIZATION: {Origin.USER_PROVIDED, Origin.SYSTEM_DEFINED},
    RecordRole.ACTION: {Origin.OBSERVED},
    RecordRole.OBSERVATION: {Origin.OBSERVED},
    RecordRole.VERIFICATION: {Origin.DERIVED},
}

# Markers used by classify_origin to infer an origin from a source string.
_EXTERNAL_MARKERS = (
    "external", "import", "document", "dataset", "reference", "web", "uri",
)
_SYSTEM_MARKERS = ("system", "builtin", "seeded", "seed", "planner")
_USER_MARKERS = ("manual", "user", "input", "human")


def coerce_origin(value) -> Optional[Origin]:
    """Coerce a value to an Origin enum (or None if invalid)."""
    if isinstance(value, Origin):
        return value
    if isinstance(value, str):
        for member in Origin:
            if member.value == value.lower():
                return member
    return None


def coerce_role(value) -> Optional[RecordRole]:
    """Coerce a value to a RecordRole enum (or None if invalid)."""
    if isinstance(value, RecordRole):
        return value
    if isinstance(value, str):
        for member in RecordRole:
            if member.value == value.lower():
                return member
    return None


def coerce_state(value) -> Optional[LifecycleState]:
    """Coerce a value to a LifecycleState enum (or None if invalid)."""
    if isinstance(value, LifecycleState):
        return value
    if isinstance(value, str):
        for member in LifecycleState:
            if member.value == value.lower():
                return member
    return None


def role_compatible_origins(role) -> Set[Origin]:
    """All origins that a given role may legitimately carry."""
    if isinstance(role, str):
        role = coerce_role(role)
    if role is None:
        return set(Origin)
    return set(_ROLE_ORIGINS.get(role, set()))


def classify_origin(source=None, declared=None, is_action_result=False,
                    default=None) -> Origin:
    """Deterministic origin classification from evidence.

    Priority:
        1. Explicit ``declared`` origin (validated).
        2. ``is_action_result`` -> OBSERVED (an action produced it).
        3. ``source`` string markers -> external / system_defined /
           user_provided.
        4. ``default`` (or USER_PROVIDED).
    """
    if declared is not None:
        origin = coerce_origin(declared)
        if origin is not None:
            return origin
    if is_action_result:
        return Origin.OBSERVED
    if isinstance(source, str):
        low = source.lower()
        if any(m in low for m in _EXTERNAL_MARKERS):
            return Origin.EXTERNAL
        if any(m in low for m in _SYSTEM_MARKERS):
            return Origin.SYSTEM_DEFINED
        if any(m in low for m in _USER_MARKERS):
            return Origin.USER_PROVIDED
    if default is not None:
        origin = coerce_origin(default)
        if origin is not None:
            return origin
    return Origin.USER_PROVIDED


RULE_EXTERNAL_NOT_EXPERIENCE = (
    "external knowledge is advisory knowledge grounded to its source; it "
    "cannot directly become an experience because an experience is the "
    "interpreted result of an executed action"
)


def check_role_origin(role, origin) -> tuple:
    """Validate that ``origin`` is legal for ``role``.

    Returns (ok, reason). The canonical trust rule (Rule 1): a record with
    origin EXTERNAL can never be recorded as EXPERIENCE.
    """
    role = coerce_role(role)
    origin = coerce_origin(origin)
    if role is None or origin is None:
        return False, "role or origin is not a valid lifecycle value"
    if role is RecordRole.EXPERIENCE and origin is Origin.EXTERNAL:
        return False, RULE_EXTERNAL_NOT_EXPERIENCE
    allowed = _ROLE_ORIGINS.get(role, set())
    if origin in allowed:
        return True, "ok"
    return False, (
        "origin %r is not a legal origin for role %r (allowed: %s)"
        % (origin.value, role.value,
           ", ".join(sorted(o.value for o in allowed)))
    )


def canonical_state_transition(current, new) -> tuple:
    """Check a lifecycle state transition against the canonical state machine.

    Returns (ok, reason). Illegal transitions are rejected rather than
    silently applied (e.g. active -> archived must go through a terminal
    state first, and archived records are immutable).
    """
    current = coerce_state(current)
    new = coerce_state(new)
    if current is None or new is None:
        return False, "current or new state is not a valid lifecycle state"
    if current is new:
        return False, "state is already %r" % new.value
    allowed = _STATE_TRANSITIONS.get(current, set())
    if new in allowed:
        return True, "ok"
    from_state = current.value
    return False, (
        "illegal lifecycle transition %r -> %r (allowed: %s)"
        % (from_state, new.value,
           ", ".join(sorted(s.value for s in allowed)) or "none")
    )


def confidence_label(confidence) -> str:
    """Deterministic confidence bucket for provenance reporting."""
    if confidence is None:
        return "unknown"
    try:
        c = float(confidence)
    except (TypeError, ValueError):
        return "unknown"
    if c >= 0.8:
        return "high"
    if c >= 0.5:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# Provenance / record model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Provenance:
    """Full provenance fingerprint attached to every lifecycle record."""

    record_id: str
    origin: Origin
    source: Optional[str] = None
    actor: Optional[str] = None
    timestamp: Optional[float] = None
    project: Optional[str] = None
    context_id: Optional[str] = None
    evidence_ids: tuple = ()
    confidence: Optional[float] = None
    lifecycle_state: LifecycleState = LifecycleState.ACTIVE
    parent_record_ids: tuple = ()
    derived_from: tuple = ()
    created_at: Optional[float] = None
    updated_at: Optional[float] = None

    def as_dict(self):
        return {
            "record_id": self.record_id,
            "origin": self.origin.value,
            "source": self.source,
            "actor": self.actor,
            "timestamp": self.timestamp,
            "project": self.project,
            "context_id": self.context_id,
            "evidence_ids": list(self.evidence_ids),
            "confidence": self.confidence,
            "lifecycle_state": self.lifecycle_state.value,
            "parent_record_ids": list(self.parent_record_ids),
            "derived_from": list(self.derived_from),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class LifecycleRecord:
    """One lifecycle-recorded entity (role + provenance + interpreted content)."""

    record_id: str
    role: RecordRole
    provenance: Provenance
    subject: Optional[str] = None
    content: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            "record_id": self.record_id,
            "role": self.role.value,
            "subject": self.subject,
            "content": self.content,
            "provenance": self.provenance.as_dict(),
        }


def derive_lifecycle_entry_id(source_kind: str, source_id: str,
                              origin, payload: dict) -> str:
    """Deterministic id for a lifecycle metadata entry.

    Same (source_kind, source_id, origin, payload) always yields the same id.
    Identifies the lifecycle *record* describing ``source_id``.
    """
    origin_value = origin.value if isinstance(origin, Origin) else str(origin)
    digest = hashlib.sha256(_canonical({
        "source_kind": source_kind,
        "source_id": source_id,
        "origin": origin_value,
        "payload": payload or {},
    }).encode("utf-8")).hexdigest()
    return "lcr_" + digest[:32]


def derive_learning_record_id(experience_ids) -> str:
    """Deterministic learning record id from a set of source experiences.

    The learning record is a derived entity: its id is stable under any
    ordering of the source experience ids, so repeated learning derivation is
    idempotent.
    """
    ids = sorted(str(x) for x in (experience_ids or ()))
    digest = hashlib.sha256(_canonical({"experience_ids": ids}).encode(
        "utf-8")).hexdigest()
    return "lrn_" + digest[:32]