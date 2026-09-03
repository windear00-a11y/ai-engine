"""Context dimension types (Phase 1).

Four canonical dimensions describe operational context. Together they make a
:class:`ContextSnapshot`:

* :class:`SystemContext`   -- host / interpreter / tool versions.
* :class:`ProjectContext`  -- project language, framework, build system.
* :class:`TaskContext`     -- the kind of work being attempted.
* :class:`TemporalContext` -- when the snapshot was captured.

These are plain, immutable value objects. The snapshot (see ``schema.py``)
is a capture at one moment in time. Context similarity (``diff.py``) is
computed over the discrete values of the system/project/task dimensions --
NEVER over the temporal dimension (time must not make identical environments
"different").
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SystemContext:
    """Host + interpreter + toolchain versions (deterministic capture)."""
    os: str = ""
    os_version: str = ""
    arch: str = ""
    python: str = ""
    # optionally: additional deterministic tool versions
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        d = {
            "os": self.os,
            "os_version": self.os_version,
            "arch": self.arch,
            "python": self.python,
        }
        if self.extra:
            d["extra"] = dict(sorted(self.extra.items()))
        return d


@dataclass(frozen=True)
class ProjectContext:
    """Project-level characteristics detected deterministically from disk."""
    language: str = ""
    framework: str = ""
    build: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        d = {
            "language": self.language,
            "framework": self.framework,
            "build": self.build,
        }
        if self.extra:
            d["extra"] = dict(sorted(self.extra.items()))
        return d


@dataclass(frozen=True)
class TaskContext:
    """The kind of work being attempted (populated from task metadata)."""
    type: str = ""
    domain: str = ""
    error_pattern: str = ""
    extra: dict = field(default_factory=dict)

    def as_dict(self):
        d = {
            "type": self.type,
            "domain": self.domain,
            "error_pattern": self.error_pattern,
        }
        if self.extra:
            d["extra"] = dict(sorted(self.extra.items()))
        return d


@dataclass(frozen=True)
class TemporalContext:
    """When the snapshot was captured (never part of similarity/id)."""
    captured_at_epoch: float = 0.0
    timezone: str = ""

    def as_dict(self):
        return {
            "captured_at_epoch": self.captured_at_epoch,
            "timezone": self.timezone,
        }
