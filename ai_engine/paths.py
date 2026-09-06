"""Data-directory and per-project path resolution (Phase 0).

Resolution order for data root:
    AI_ENGINE_DATA_DIR -> XDG_DATA_HOME/ai-engine -> ~/.ai-engine

All helpers are stdlib-only, deterministic, and do not import
tools/permissions or intelligence modules.

Project isolation:
    <data_root>/<project_id>/knowledge.db
    <data_root>/<project_id>/context.db
    <data_root>/<project_id>/evidence.db
    <data_root>/<project_id>/experience.db
    <data_root>/<project_id>/engine_state.db
    <data_root>/<project_id>/snapshots/
    <data_root>/<project_id>/backups/

projects.json lives at <data_root>/projects.json
users.json    lives at <data_root>/users.json (minimal, v1 single-user)

Every project_id is validated; paths that would escape data_root are
rejected fail-closed.
"""

import json
import os
import re
import threading
import time

# Stable project id validation: lowercase alnum, dash, underscore, 1..64.
_PROJECT_ID_RE = re.compile(r"^[a-z0-9_-]{1,64}$")
# Also allow single "default" as the canonical default project.
DEFAULT_PROJECT_ID = "default"

# Database file names that are per-project.
_KNOWN_DBS = (
    "knowledge.db",
    "context.db",
    "evidence.db",
    "experience.db",
    "engine_state.db",
    "activity.db",
)


def _expand(p):
    return os.path.expanduser(p)


def get_data_root():
    """Resolve data root deterministically.

    Priority:
        1. $AI_ENGINE_DATA_DIR if non-empty after strip
        2. $XDG_DATA_HOME/ai-engine if non-empty after strip
        3. ~/.ai-engine
    Always returns an absolute path (no trailing slash, not resolved symlink).
    Environment values are expanded with ~ and abspath'd.
    """
    v = os.environ.get("AI_ENGINE_DATA_DIR", "")
    if isinstance(v, str) and v.strip():
        return os.path.abspath(_expand(v.strip()))
    v2 = os.environ.get("XDG_DATA_HOME", "")
    if isinstance(v2, str) and v2.strip():
        base = os.path.abspath(_expand(v2.strip()))
        return os.path.join(base, "ai-engine")
    return os.path.join(_expand("~"), ".ai-engine")


def get_users_registry(data_root=None):
    root = data_root or get_data_root()
    return os.path.join(root, "users.json")


def get_projects_registry(data_root=None):
    root = data_root or get_data_root()
    return os.path.join(root, "projects.json")


def _validate_project_id(project_id):
    if not isinstance(project_id, str) or not project_id:
        raise ValueError("project_id must be a non-empty string")
    if not _PROJECT_ID_RE.match(project_id):
        raise ValueError(
            f"invalid project_id {project_id!r}: must match {_PROJECT_ID_RE.pattern}"
        )
    # Reject path traversal even if regex would allow (defensive).
    if project_id in (".", "..") or "/" in project_id or "\\" in project_id:
        raise ValueError(f"invalid project_id {project_id!r}: path traversal")
    return project_id


def _ensure_inside_data_root(path, data_root):
    """Fail closed if `path` would escape `data_root` (symlink-aware)."""
    # Use realpath for both to handle symlink components.
    # For non-existent path, realpath falls back to abspath.
    real_root = os.path.realpath(data_root)
    real_path = os.path.realpath(path)
    # commonpath raises ValueError on different drives (Windows) -> fail closed
    try:
        common = os.path.commonpath([real_root, real_path])
    except ValueError:
        raise ValueError(f"project path {path!r} escapes data root {data_root!r}")
    if common != real_root:
        raise ValueError(f"project path {path!r} escapes data root {data_root!r}")
    return path


def get_project_dir(project_id, data_root=None):
    """Return absolute project directory for `project_id`.

    Validates project_id and ensures the result is inside data_root.
    Does NOT create the directory.
    """
    pid = _validate_project_id(project_id)
    root = data_root or get_data_root()
    # Normalize root to absolute
    root = os.path.abspath(root)
    proj_dir = os.path.join(root, pid)
    _ensure_inside_data_root(proj_dir, root)
    return proj_dir


def get_knowledge_db(project_id, data_root=None):
    return os.path.join(get_project_dir(project_id, data_root), "knowledge.db")


def get_context_db(project_id, data_root=None):
    return os.path.join(get_project_dir(project_id, data_root), "context.db")


def get_evidence_db(project_id, data_root=None):
    return os.path.join(get_project_dir(project_id, data_root), "evidence.db")


def get_experience_db(project_id, data_root=None):
    return os.path.join(get_project_dir(project_id, data_root), "experience.db")


def get_engine_state_db(project_id, data_root=None):
    return os.path.join(get_project_dir(project_id, data_root), "engine_state.db")


def get_activity_db(project_id, data_root=None):
    return os.path.join(get_project_dir(project_id, data_root), "activity.db")


def get_snapshots_dir(project_id, data_root=None):
    return os.path.join(get_project_dir(project_id, data_root), "snapshots")


def get_backups_dir(project_id, data_root=None):
    return os.path.join(get_project_dir(project_id, data_root), "backups")


def get_db_path(project_id, db_name, data_root=None):
    """Generic helper for known db names (validates db_name).

    `db_name` must be one of _KNOWN_DBS to avoid arbitrary path creation.
    """
    if db_name not in _KNOWN_DBS:
        raise ValueError(f"unknown db_name {db_name!r}: must be one of {_KNOWN_DBS}")
    return os.path.join(get_project_dir(project_id, data_root), db_name)


def list_known_dbs():
    return tuple(_KNOWN_DBS)


# ---------------------------------------------------------------------------
# Minimal project-registry foundation (Phase 0, no user/account system)
# ---------------------------------------------------------------------------

def _default_registry_data():
    now = time.time()
    return {
        "projects": [
            {
                "project_id": DEFAULT_PROJECT_ID,
                "display_name": "Default",
                "vocabulary_id": "code_v1",
                "created_at_epoch": now,
            }
        ],
        "default_project": DEFAULT_PROJECT_ID,
    }


def load_projects_registry(data_root=None):
    """Load projects.json if present, else return None (caller decides).

    Returns dict or None. Does not create files.
    """
    reg_path = get_projects_registry(data_root)
    if not os.path.exists(reg_path):
        return None
    # Reject symlink that escapes data_root for safety.
    try:
        _ensure_inside_data_root(os.path.realpath(reg_path), os.path.realpath(data_root or get_data_root()))
    except ValueError:
        raise
    try:
        with open(reg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"invalid projects registry {reg_path!r}: {e}")
    # Minimal validation
    if not isinstance(data, dict) or "projects" not in data:
        raise ValueError(f"invalid projects registry {reg_path!r}: missing 'projects'")
    return data


def save_projects_registry(data, data_root=None):
    """Atomically save projects.json (write temp + rename).

    Validates that `data` contains at least `projects` list.
    """
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        raise ValueError("projects registry must be dict with 'projects' list")
    reg_path = get_projects_registry(data_root)
    root = data_root or get_data_root()
    os.makedirs(os.path.dirname(reg_path) or ".", exist_ok=True)
    # Ensure registry path is inside data_root
    _ensure_inside_data_root(os.path.realpath(reg_path), os.path.realpath(root))
    tmp = reg_path + f".tmp.{os.getpid()}.{threading.get_ident()}"
    # Deterministic JSON: sort_keys, no trailing newline ambiguity
    payload = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp, reg_path)
    return reg_path


def ensure_default_project(data_root=None):
    """Ensure data_root exists and projects.json contains default project.

    Safe to call repeatedly (idempotent). Returns registry dict.
    Fail closed on collision (existing projects.json with different default).
    """
    root = data_root or get_data_root()
    os.makedirs(root, exist_ok=True)
    # Also ensure project directory exists for default
    proj_dir = get_project_dir(DEFAULT_PROJECT_ID, root)
    os.makedirs(proj_dir, exist_ok=True)
    os.makedirs(get_snapshots_dir(DEFAULT_PROJECT_ID, root), exist_ok=True)
    os.makedirs(get_backups_dir(DEFAULT_PROJECT_ID, root), exist_ok=True)

    reg = load_projects_registry(root)
    if reg is None:
        reg = _default_registry_data()
        # Ensure the directory for the default project id matches the created one
        save_projects_registry(reg, root)
        return reg
    # Already exists: ensure default entry is present
    pids = {p.get("project_id") for p in reg.get("projects", []) if isinstance(p, dict)}
    if DEFAULT_PROJECT_ID not in pids:
        reg["projects"].append(
            {
                "project_id": DEFAULT_PROJECT_ID,
                "display_name": "Default",
                "vocabulary_id": "code_v1",
                "created_at_epoch": time.time(),
            }
        )
        save_projects_registry(reg, root)
    return reg


def create_project(project_id, display_name=None, vocabulary_id="code_v1", data_root=None):
    """Create a new project entry. Fail closed if project_id already exists.

    Creates project directory and snapshots/backups subdirs.
    Returns the created project dict.
    Raises ValueError on collision or invalid id.
    """
    pid = _validate_project_id(project_id)
    root = data_root or get_data_root()
    os.makedirs(root, exist_ok=True)
    reg = load_projects_registry(root)
    if reg is None:
        reg = _default_registry_data()
        # If we are creating a non-default project and registry didn't exist,
        # we need to ensure default is there first, then add the new one.
        if pid != DEFAULT_PROJECT_ID:
            # default already in _default_registry_data
            pass
        save_projects_registry(reg, root)
        # Re-load to ensure consistency
        reg = load_projects_registry(root)

    existing = {p.get("project_id") for p in reg.get("projects", []) if isinstance(p, dict)}
    if pid in existing:
        raise ValueError(f"project_id {pid!r} already exists (collision)")

    entry = {
        "project_id": pid,
        "display_name": display_name or pid,
        "vocabulary_id": vocabulary_id,
        "created_at_epoch": time.time(),
    }
    reg["projects"].append(entry)
    # Ensure project dirs
    proj_dir = get_project_dir(pid, root)
    os.makedirs(proj_dir, exist_ok=True)
    os.makedirs(get_snapshots_dir(pid, root), exist_ok=True)
    os.makedirs(get_backups_dir(pid, root), exist_ok=True)
    save_projects_registry(reg, root)
    return entry


def list_projects(data_root=None):
    reg = load_projects_registry(data_root)
    if reg is None:
        return []
    return list(reg.get("projects", []))


def get_project_entry(project_id, data_root=None):
    pid = _validate_project_id(project_id)
    reg = load_projects_registry(data_root)
    if reg is None:
        return None
    for p in reg.get("projects", []):
        if isinstance(p, dict) and p.get("project_id") == pid:
            return p
    return None
