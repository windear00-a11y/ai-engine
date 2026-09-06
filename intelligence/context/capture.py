"""Context snapshot capture — generic Persistent Intelligence Context (Phase 4).

``capture_context`` reads environment state deterministically to build a
:class:`ContextSnapshot` with 8 generic dimensions. Two captures of the
same situation produce the same 7 non-temporal dimensions and therefore
the same deterministic ``context_id`` (temporal differs but is excluded).

Capture is READ-ONLY: never executes code, never mutates data.
Coding-specific assumptions (language/framework) are kept as optional
extra detection when project_root is provided, but not required.
"""

import os
import platform
import time

from intelligence.context.schema import ContextSnapshot, derive_context_id
from intelligence.context.types import (
    ActorContext,
    AffectiveContext,
    EnvironmentContext,
    ProjectContext,
    SocialContext,
    SourceContext,
    SpatialContext,
    TemporalContext,
)

# Optional coding detection (kept for backward compat, not required for generic)
_KNOWN_FRAMEWORKS = ("django", "flask", "fastapi", "torch", "tensorflow", "pandas")
_BUILD_FILES = ("pyproject.toml", "setup.py", "setup.cfg", "package.json", "Cargo.toml", "go.mod", "build.gradle", "pom.xml", "Makefile")
_BUILD_VALUE = {"pyproject.toml": "pyproject", "setup.py": "setuptools", "setup.cfg": "setuptools", "package.json": "npm", "Cargo.toml": "cargo", "go.mod": "go", "build.gradle": "gradle", "pom.xml": "maven", "Makefile": "make"}


def _detect_environment() -> EnvironmentContext:
    return EnvironmentContext(
        os=platform.system().lower(),
        os_version=platform.release(),
        arch=platform.machine(),
        python=platform.python_version(),
    )


def _detect_project_generic(project_root=None, task_metadata=None):
    """Detect project identity generically.

    If project_root provided, optionally detect language/framework (legacy).
    Otherwise return minimal project with project_id/vocabulary if in task_metadata.
    """
    extra = {}
    language = ""
    framework = ""
    build = ""
    project_id = ""
    vocabulary_id = ""

    # Extract generic project hints from task_metadata if provided
    if isinstance(task_metadata, dict):
        project_id = str(task_metadata.get("project_id", "") or "")
        vocabulary_id = str(task_metadata.get("vocabulary_id", "") or "")

    if project_root is not None:
        try:
            files = {f.lower() for f in os.listdir(project_root)}
        except (OSError, ValueError):
            files = set()
        if any(n in files for n in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "tox.ini")):
            language = "python"
        elif "package.json" in files:
            language = "javascript"
        elif "cargo.toml" in files:
            language = "rust"
        elif "go.mod" in files:
            language = "go"

        present = [b for b in _BUILD_FILES if b.lower() in files]
        if present:
            build = _BUILD_VALUE[present[0]]

        for fw in _KNOWN_FRAMEWORKS:
            if fw == "django" and os.path.isfile(os.path.join(project_root, "manage.py")):
                framework = fw
                break

    d = {}
    if project_id:
        d["project_id"] = project_id
    if vocabulary_id:
        d["vocabulary_id"] = vocabulary_id
    # Legacy fields kept as optional extra, not required
    if language:
        d["language"] = language
    if framework:
        d["framework"] = framework
    if build:
        d["build"] = build
    # Return via ProjectContext (handles project_id/vocab)
    return ProjectContext(
        project_id=project_id,
        vocabulary_id=vocabulary_id,
        language=language,
        framework=framework,
        build=build,
        extra=extra,
    )


def _detect_source(task_metadata=None, project_root=None):
    md = dict(task_metadata or {})
    # Map legacy task fields to source, plus generic adapter fields
    adapter = str(md.get("adapter", "") or md.get("source", "") or "")
    uri = str(md.get("uri", "") or "")
    payload_hash = str(md.get("payload_hash", "") or "")
    return SourceContext(
        adapter=adapter,
        uri=uri,
        payload_hash=payload_hash,
        type=str(md.get("type", "") or ""),
        domain=str(md.get("domain", "") or ""),
        error_pattern=str(md.get("error_pattern", "") or ""),
        extra={k: v for k, v in md.items() if k not in ("adapter", "uri", "payload_hash", "type", "domain", "error_pattern", "project_id", "vocabulary_id", "user_id", "location", "with", "mood")},
    )


def _detect_actor(task_metadata=None):
    md = dict(task_metadata or {})
    user_id = str(md.get("user_id", "") or md.get("actor_id", "") or "")
    return ActorContext(user_id=user_id)


def _detect_spatial(task_metadata=None):
    md = dict(task_metadata or {})
    loc = str(md.get("location", "") or "")
    return SpatialContext(location=loc)


def _detect_social(task_metadata=None):
    md = dict(task_metadata or {})
    with_user = str(md.get("with", "") or md.get("with_user", "") or "")
    return SocialContext(with_user=with_user)


def _detect_affective(task_metadata=None):
    md = dict(task_metadata or {})
    mood = str(md.get("mood", "") or "")
    return AffectiveContext(mood=mood)


def _detect_temporal() -> TemporalContext:
    return TemporalContext(captured_at_epoch=time.time(), timezone=time.tzname[0])


def capture_context(project_root=None, task_metadata=None) -> ContextSnapshot:
    """Capture a deterministic generic context snapshot.

    Parameters
    ----------
    project_root : str, optional
        Directory for optional language/framework detection (legacy, not required).
    task_metadata : dict, optional
        Generic hints: project_id, vocabulary_id, adapter, uri, payload_hash,
        user_id, location, with, mood, plus legacy type/domain/error_pattern.

    Returns ContextSnapshot with 8 dimensions (temporal excluded from id).
    """
    env = _detect_environment()
    proj = _detect_project_generic(project_root, task_metadata)
    src = _detect_source(task_metadata, project_root)
    actor = _detect_actor(task_metadata)
    spatial = _detect_spatial(task_metadata)
    social = _detect_social(task_metadata)
    affective = _detect_affective(task_metadata)
    temporal = _detect_temporal()

    return ContextSnapshot.build(
        environment=env.as_dict(),
        project=proj.as_dict(),
        source=src.as_dict(),
        actor=actor.as_dict(),
        spatial=spatial.as_dict(),
        social=social.as_dict(),
        affective=affective.as_dict(),
        temporal=temporal.as_dict(),
        captured_at_epoch=temporal.captured_at_epoch,
    )
