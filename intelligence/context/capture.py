"""Context snapshot capture from the environment (Phase 1).

``capture_context`` reads environment state deterministically to build a
:class:`ContextSnapshot`. Two captures of the same environment produce the
same system/project/task values and therefore the same deterministic
``context_id`` (the temporal dimension differs but is excluded from the id).

Capture is READ-ONLY: it reads the host, interpreter, and project filesystem;
it never executes code, never mutates any existing data, and holds no
execution authority.
"""

import os
import platform
import time

from intelligence.context.schema import ContextSnapshot, derive_context_id
from intelligence.context.types import (
    ProjectContext,
    SystemContext,
    TaskContext,
    TemporalContext,
)

# Fixed project characteristic weights/name detection maps.
_KNOWN_FRAMEWORKS = (
    "django", "flask", "fastapi", "torch", "tensorflow", "pandas",
)
_BUILD_FILES = (
    "pyproject.toml", "setup.py", "setup.cfg", "package.json",
    "Cargo.toml", "go.mod", "build.gradle", "pom.xml", "Makefile",
)
_BUILD_VALUE = {
    "pyproject.toml": "pyproject",
    "setup.py": "setuptools",
    "setup.cfg": "setuptools",
    "package.json": "npm",
    "Cargo.toml": "cargo",
    "go.mod": "go",
    "build.gradle": "gradle",
    "pom.xml": "maven",
    "Makefile": "make",
}


def _detect_system() -> SystemContext:
    return SystemContext(
        os=platform.system().lower(),
        os_version=platform.release(),
        arch=platform.machine(),
        python=platform.python_version(),
    )


def _detect_project(project_root):
    root = project_root
    if root is None:
        return ProjectContext()
    d = {
        "language": "",
        "framework": "",
        "build": "",
        "extra": {},
    }
    files = set()
    try:
        for entry in os.listdir(root):
            files.add(entry.lower())
    except (OSError, ValueError):
        return ProjectContext()

    if any(name in files for name in
           ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
            "setup.py", "tox.ini")):
        d["language"] = "python"
    elif "package.json" in files:
        d["language"] = "javascript"
    elif "cargo.toml" in files:
        d["language"] = "rust"
    elif "go.mod" in files:
        d["language"] = "go"
    else:
        d["language"] = "unknown"

    present_build = [b for b in _BUILD_FILES if b.lower() in files]
    if present_build:
        d["build"] = _BUILD_VALUE[present_build[0]]

    framework = ""
    for fw in _KNOWN_FRAMEWORKS:
        marker = _framework_marker(fw, root)
        if marker:
            framework = fw
            break
    d["framework"] = framework
    return ProjectContext(**d)


def _framework_marker(framework, root):
    """Return truthy if a framework marker is present in the project root."""
    markers = {
        "django": "manage.py",
        "flask": None,   # detected via dependency below is skipped; keep none
        "fastapi": None,
        "torch": None,
        "tensorflow": None,
        "pandas": None,
    }
    marker = markers.get(framework)
    if marker and os.path.isfile(os.path.join(root, marker)):
        return marker
    return None


def _detect_task(task_metadata):
    md = dict(task_metadata or {})
    return TaskContext(
        type=str(md.get("type", "")),
        domain=str(md.get("domain", "")),
        error_pattern=str(md.get("error_pattern", "")),
    )


def _detect_temporal() -> TemporalContext:
    return TemporalContext(
        captured_at_epoch=time.time(),
        timezone=time.tzname[0],
    )


def capture_context(project_root=None, task_metadata=None) -> ContextSnapshot:
    """Capture a deterministic operational-context snapshot.

    Parameters
    ----------
    project_root : str, optional
        Directory whose language/framework/build is detected. When None, the
        project dimension is empty.
    task_metadata : dict, optional
        Optional ``{type, domain, error_pattern, extra}`` describing the task.

    Returns
    -------
    ContextSnapshot
    """
    system = _detect_system()
    project = _detect_project(project_root)
    task = _detect_task(task_metadata)
    temporal = _detect_temporal()
    s = system.as_dict()
    p = project.as_dict()
    t = task.as_dict()
    return ContextSnapshot.build(
        system=s,
        project=p,
        task=t,
        temporal=temporal.as_dict(),
        captured_at_epoch=temporal.captured_at_epoch,
    )
