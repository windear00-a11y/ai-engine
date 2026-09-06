"""ContextSnapshot data model — generic Persistent Intelligence Context (Phase 4).

Provides generic 8-dimensional context plus deterministic context_id.

Dimensions (architecture spec, Phase 4):
    environment — host / OS / device (e.g. {"os": "linux", "device": "local"})
    project     — project identity (e.g. {"project_id": "default", "vocabulary_id": "diary_v1"})
    source      — capture source (e.g. {"adapter": "manual", "uri": "manual://act_..."})
    actor       — who (e.g. {"user_id": "local"})
    spatial     — where (e.g. {"location": null})
    social      — with whom (e.g. {"with": []})
    affective   — mood / affective state (e.g. {"mood": null})
    temporal    — when (e.g. {"captured_at_epoch": 1234567890.0}) — EXCLUDED from id

Determinism of context_id
-------------------------
``context_id`` is SHA-256 over canonical JSON of the 7 non-temporal
dimensions only. Temporal is deliberately excluded: two captures of the
same situation at different times yield the SAME context_id (idempotent),
even though captured_at_epoch differs.

Backward compat
---------------
Legacy callers used 4 dimensions: system/project/task/temporal.
For compatibility, build() still accepts system/project/task and maps:
    system    -> environment
    project   -> project (same)
    task      -> source (and actor if present)
Old derive_context_id(system, project, task) remains available but delegates
to the generic derive.
"""

import hashlib
import json
from dataclasses import dataclass, field


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _normalize_dim(d):
    """Normalize a dimension dict for hashing: sorted keys, ensure dict."""
    if d is None:
        return {}
    if not isinstance(d, dict):
        raise ValueError("context dimension must be a dict")
    # Shallow copy with sorted keys, ensure JSON-serializable values
    # Fail closed on uncontrolled dump: reject non-dict top-level
    # Allow nested dicts/lists/primitives but require keys are strings
    for k in d.keys():
        if not isinstance(k, str):
            raise ValueError("context dimension keys must be strings")
    return dict(d)


def derive_context_id(environment=None, project=None, source=None, actor=None,
                      spatial=None, social=None, affective=None,
                      system=None, task=None):
    """Deterministic context_id over the 7 generic dimensions (temporal excluded).

    Supports both new 7-dim and legacy 3-dim (system/project/task) for compat.
    If legacy args (system/task) are given, they are mapped:
        system -> environment
        task   -> source (plus actor if actor not separately given)

    Temporal is never included.
    """
    # Map legacy to generic if needed
    if environment is None and system is not None:
        environment = system
    if source is None and task is not None:
        # task may contain activity_type/source etc -> treat as source
        if isinstance(task, dict):
            source = dict(task)
        else:
            source = task

    env = _normalize_dim(environment or {})
    proj = _normalize_dim(project or {})
    src = _normalize_dim(source or {})
    act = _normalize_dim(actor or {})
    spat = _normalize_dim(spatial or {})
    soc = _normalize_dim(social or {})
    aff = _normalize_dim(affective or {})

    payload = {
        "environment": env,
        "project": proj,
        "source": src,
        "actor": act,
        "spatial": spat,
        "social": soc,
        "affective": aff,
    }
    digest = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    return "ctx_" + digest[:32]


# Legacy derive for backward compat (old 4-dim)
def _legacy_derive_context_id(system, project, task):
    return derive_context_id(environment=system, project=project, source=task, actor={}, spatial={}, social={}, affective={})


@dataclass(frozen=True)
class ContextSnapshot:
    """An immutable capture of generic operational context at a point in time.

    New generic dimensions (Phase 4):
        environment, project, source, actor, spatial, social, affective, temporal
    Legacy aliases (for compat):
        system -> environment
        task   -> source

    All dimensions are dicts. Temporal is excluded from context_id.
    """
    environment: dict = field(default_factory=dict)
    project: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)
    actor: dict = field(default_factory=dict)
    spatial: dict = field(default_factory=dict)
    social: dict = field(default_factory=dict)
    affective: dict = field(default_factory=dict)
    temporal: dict = field(default_factory=dict)
    context_id: str = ""
    captured_at_epoch: float = 0.0

    # Legacy aliases as properties (for old tests that read .system / .task)
    @property
    def system(self):
        return self.environment

    @property
    def task(self):
        # Legacy task was source-like; return source for compat
        return self.source

    @classmethod
    def build(cls, system=None, project=None, task=None, temporal=None,
              captured_at_epoch=None, environment=None, source=None, actor=None,
              spatial=None, social=None, affective=None, **kwargs):
        """Build a snapshot deterministically.

        Supports both new and legacy signatures:

        New (generic):
            ContextSnapshot.build(environment={}, project={}, source={}, actor={},
                                  spatial={}, social={}, affective={}, temporal={})

        Legacy (compat):
            ContextSnapshot.build(system={}, project={}, task={}, temporal={})

        Legacy `system` maps to `environment`, `task` maps to `source`.
        """
        # Handle legacy <-> new aliases (deterministic, fail closed on conflict)
        if environment is not None and system is not None:
            raise ValueError("provide either environment or system, not both")
        if source is not None and task is not None:
            raise ValueError("provide either source or task, not both")
        # Map legacy to generic if generic not provided
        if environment is None and system is not None:
            environment = system
        if source is None and task is not None:
            source = task

        # Reject unexpected kwargs
        if kwargs:
            raise ValueError(f"unexpected context dimensions: {sorted(kwargs.keys())}")

        # Normalize each dimension (reject non-dict, ensure controlled)
        env = _normalize_dim(environment or {})
        proj = _normalize_dim(project or {})
        src = _normalize_dim(source or {})
        act = _normalize_dim(actor or {})
        spat = _normalize_dim(spatial or {})
        soc = _normalize_dim(social or {})
        aff = _normalize_dim(affective or {})
        temp = _normalize_dim(temporal or {})

        # Validate no uncontrolled dump: each dimension should be shallow dict of primitives
        # We allow nested dicts but require they are JSON-serializable; already ensured via _canonical
        # Fail closed if any dimension contains disallowed types like function?
        # _canonical will raise if not serializable, but we check here via json dumps
        for name, dim in [("environment", env), ("project", proj), ("source", src), ("actor", act), ("spatial", spat), ("social", soc), ("affective", aff), ("temporal", temp)]:
            try:
                _canonical(dim)
            except Exception as e:
                raise ValueError(f"context dimension {name!r} not JSON-serializable: {e}")

        context_id = derive_context_id(
            environment=env, project=proj, source=src, actor=act,
            spatial=spat, social=soc, affective=aff
        )
        return cls(
            environment=env,
            project=proj,
            source=src,
            actor=act,
            spatial=spat,
            social=soc,
            affective=aff,
            temporal=temp,
            context_id=context_id,
            captured_at_epoch=captured_at_epoch if captured_at_epoch is not None else (temp.get("captured_at_epoch", 0.0) if isinstance(temp, dict) else 0.0),
        )

    def as_dict(self):
        return {
            "context_id": self.context_id,
            "captured_at_epoch": self.captured_at_epoch,
            "environment": self.environment,
            "project": self.project,
            "source": self.source,
            "actor": self.actor,
            "spatial": self.spatial,
            "social": self.social,
            "affective": self.affective,
            "temporal": self.temporal,
            # Legacy aliases for compat readers
            "system": self.environment,
            "task": self.source,
        }

    def to_json(self):
        return json.dumps(self.as_dict(), sort_keys=True, default=str)
