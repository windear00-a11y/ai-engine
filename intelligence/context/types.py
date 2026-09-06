"""Context dimension types — generic Persistent Intelligence Context (Phase 4).

Eight canonical dimensions describe generic context. Together they make a
:class:`ContextSnapshot`:

* :class:`EnvironmentContext` — host / device / OS
* :class:`ProjectContext`     — project identity (project_id, vocabulary)
* :class:`SourceContext`      — capture source (adapter, uri)
* :class:`ActorContext`       — who (user_id)
* :class:`SpatialContext`     — where
* :class:`SocialContext`      — with whom
* :class:`AffectiveContext`   — mood / affective state
* :class:`TemporalContext`    — when (excluded from similarity/id)

Legacy aliases (for backward compat):
* :class:`SystemContext`   -> EnvironmentContext
* :class:`TaskContext`     -> SourceContext

These are plain, immutable value objects. The snapshot (see ``schema.py``)
is a capture at one moment in time. Context similarity (``diff.py``) is
computed over the 7 non-temporal dimensions — NEVER over temporal.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class EnvironmentContext:
    """Host / device / OS (deterministic capture)."""
    os: str = ""
    os_version: str = ""
    arch: str = ""
    python: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        d = {"os": self.os, "os_version": self.os_version, "arch": self.arch, "python": self.python}
        if self.extra:
            d["extra"] = dict(sorted(self.extra.items()))
        return d


# Legacy alias
SystemContext = EnvironmentContext


@dataclass(frozen=True)
class ProjectContext:
    """Project-level identity (deterministic)."""
    project_id: str = ""
    vocabulary_id: str = ""
    # Legacy coding fields (optional, for backward compat)
    language: str = ""
    framework: str = ""
    build: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        d = {}
        if self.project_id:
            d["project_id"] = self.project_id
        if self.vocabulary_id:
            d["vocabulary_id"] = self.vocabulary_id
        if self.language:
            d["language"] = self.language
        if self.framework:
            d["framework"] = self.framework
        if self.build:
            d["build"] = self.build
        if self.extra:
            d["extra"] = dict(sorted(self.extra.items()))
        return d


@dataclass(frozen=True)
class SourceContext:
    """Capture source (adapter, uri, payload hash)."""
    adapter: str = ""
    uri: str = ""
    payload_hash: str = ""
    type: str = ""  # legacy task type
    domain: str = ""  # legacy
    error_pattern: str = ""  # legacy
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        d = {}
        if self.adapter:
            d["adapter"] = self.adapter
        if self.uri:
            d["uri"] = self.uri
        if self.payload_hash:
            d["payload_hash"] = self.payload_hash
        if self.type:
            d["type"] = self.type
        if self.domain:
            d["domain"] = self.domain
        if self.error_pattern:
            d["error_pattern"] = self.error_pattern
        if self.extra:
            d["extra"] = dict(sorted(self.extra.items()))
        return d


# Legacy alias
TaskContext = SourceContext


@dataclass(frozen=True)
class ActorContext:
    """Who — actor identity."""
    user_id: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        d = {}
        if self.user_id:
            d["user_id"] = self.user_id
        if self.extra:
            d["extra"] = dict(sorted(self.extra.items()))
        return d


@dataclass(frozen=True)
class SpatialContext:
    """Where — spatial location."""
    location: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        d = {}
        if self.location:
            d["location"] = self.location
        if self.extra:
            d["extra"] = dict(sorted(self.extra.items()))
        return d


@dataclass(frozen=True)
class SocialContext:
    """With whom — social context."""
    with_user: str = ""  # avoid keyword 'with'
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        d = {}
        if self.with_user:
            d["with"] = self.with_user
        if self.extra:
            d["extra"] = dict(sorted(self.extra.items()))
        # Handle legacy "with" key directly
        if "with" in self.extra:
            d["with"] = self.extra["with"]
        return d


@dataclass(frozen=True)
class AffectiveContext:
    """Mood / affective state."""
    mood: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        d = {}
        if self.mood:
            d["mood"] = self.mood
        if self.extra:
            d["extra"] = dict(sorted(self.extra.items()))
        return d


@dataclass(frozen=True)
class TemporalContext:
    """When the snapshot was captured (never part of similarity/id)."""
    captured_at_epoch: float = 0.0
    timezone: str = ""

    def as_dict(self):
        return {"captured_at_epoch": self.captured_at_epoch, "timezone": self.timezone}
