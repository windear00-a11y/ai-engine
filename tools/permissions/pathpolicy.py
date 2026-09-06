"""Path policy: deterministic zone classification on top of Workspace.

Every path first passes through :meth:`tools.permissions.fs.Workspace.resolve`,
which is the single trust boundary for path confinement (traversal, absolute
outside paths, symlink escapes). This module then classifies the RESOLVED
absolute path into a zone and derives its protection mode.

Zones
-----
* workspace_writeable -- normal editable files
* workspace_readonly  -- readable but not writable
* protected           -- high-value/secret: write requires approval + snapshot
* blocked             -- must never be touched (read or write)
* outside_workspace   -- resolved outside root (denied; Workspace prevents)

Zone matching is name/glob based using the path RELATIVE to the workspace
root, normalized to forward slashes. A spec ending in ``/**`` matches the
directory and everything under it recursively.
"""

import fnmatch
import os

from tools.permissions.fs import Workspace, PathError
from tools.permissions.decisions import (
    REASON_OUTSIDE_WORKSPACE,
    REASON_BLOCKED,
    REASON_READONLY,
)


class Zone:
    WORKSPACE_WRITEABLE = "workspace_writeable"
    WORKSPACE_READONLY = "workspace_readonly"
    PROTECTED = "protected"
    BLOCKED = "blocked"
    OUTSIDE = "outside_workspace"


# Hard write invariants: expressed as relative <path> -> zone. These are
# enforced in addition to policy so that the frozen production knowledge DB
# is never written no matter how the policy file is edited.
_HARD_WRITE_INVARIANT_PATHS = (
    ("database/knowledge.db", Zone.BLOCKED),
    ("database/knowledge.db.backup", Zone.BLOCKED),
)


def _norm(rel_path):
    """Normalize a workspace-relative path to forward slashes, no leading /."""
    rel = rel_path.replace("\\", "/").lstrip("/")
    return rel


def _match_spec(rel_path, spec):
    """Match a normalized relative path against one policy spec.

    ``spec`` may end with ``/**`` to indicate "this directory recursively".
    Otherwise it is matched with fnmatch against the full path so that
    ``*.pem`` matches a pem anywhere, ``.env`` matches the exact root .env,
    etc.
    """
    if spec.endswith("/**"):
        base = spec[:-3]  # strip "/**"
        if rel_path == base or rel_path.startswith(base + "/"):
            return True
        return False
    # fnmatch on the whole relative path tolerates leading ** wildcards and
    # exact names.
    return fnmatch.fnmatch(rel_path, spec) or fnmatch.fnmatch(rel_path, spec + "/**")


def _classify(policy, rel_path):
    """Return the policy-declared zone for a normalized relative path."""
    zones = getattr(policy, "zones", {}) or {}
    # Specific zones take precedence over generic ones in this order:
    # blocked > protected > workspace_readonly > workspace_writeable.
    for zone in (Zone.BLOCKED, Zone.PROTECTED, Zone.WORKSPACE_READONLY):
        specs = zones.get(zone) or []
        for spec in specs:
            if _match_spec(rel_path, spec):
                return zone
    return Zone.WORKSPACE_WRITEABLE


class PathPolicy:
    """Wraps a Workspace plus a Policy to answer zone/mode questions.

    All queries pass paths through ``self.ws.resolve`` first, so escaping
    inputs raise :class:`PathError` before any zone logic runs.
    """

    def __init__(self, root, policy=None, ws=None):
        self.ws = ws if ws is not None else Workspace(root)
        if policy is None:
            from tools.permissions.policy import Policy
            policy = Policy()
        self.policy = policy
        self.root = self.ws.root

    # -- resolve -----------------------------------------------------------

    def resolve(self, path):
        """Resolve ``path`` through Workspace (raises PathError on escape)."""
        return self.ws.resolve(path)

    def _rel_of_abs(self, abs_path):
        return _norm(self.ws.rel(abs_path))

    # -- host-mode (read/write gate decisions) -----------------------------

    def mode_for(self, abs_path):
        """Return a host-mode dict for a resolved absolute path.

        Modes are used by the read and write gates:
          * deny      -- must not be read/written (blocked)
          * read_only -- may be read, not written (workspace_readonly)
          * writeable -- writable (workspace_writeable, protected)

        ``protected`` is represented separately so the write gate can require
        approval + snapshot on top of writeability.
        """
        rel = self._rel_of_abs(abs_path)
        zone = _classify(self.policy, rel)
        if zone == Zone.BLOCKED:
            return {"zone": zone, "mode": "deny",
                    "reason_code": REASON_BLOCKED, "rel": rel}
        if zone == Zone.WORKSPACE_READONLY:
            return {"zone": zone, "mode": "read_only",
                    "reason_code": REASON_READONLY, "rel": rel}
        if zone == Zone.PROTECTED:
            return {"zone": zone, "mode": "writeable",
                    "protected": True, "rel": rel,
                    "reason_code": "protected"}
        return {"zone": zone, "mode": "writeable",
                "protected": False, "rel": rel}

    def read_decision(self, path):
        """Read gate: deterministic Decision for reading ``path``.

        READ is allowed within the workspace except for blocked paths.
        Raises :class:`PathError` if the path escapes the workspace.
        """
        abs_path = self.resolve(path)
        info = self.mode_for(abs_path)
        if info["mode"] == "deny":
            from tools.permissions.decisions import deny
            return deny(REASON_BLOCKED)
        from tools.permissions.decisions import allow
        return allow()

    def write_zone(self, path):
        """WRITE zone classification for a (resolved) path.

        Returns a dict with the zone info, or None if the path escapes.
        Does not decide approval -- the approval gate does that.
        """
        try:
            abs_path = self.resolve(path)
        except PathError:
            return None
        info = self.mode_for(abs_path)
        info["abs_path"] = abs_path
        return info


# -- hard write guard -------------------------------------------------------

def hard_write_guard(abs_path, root):
    """Hard invariant: is ``abs_path`` (under ``root``) a frozen knowledge DB?

    Returns True if the path should be hard-blocked from writes regardless of
    policy. This is a belt-and-suspenders over :class:`PathPolicy` so the
    production ``database/knowledge.db`` and its backup can never be written.
    """
    try:
        rel = os_relpath(abs_path, root)
    except ValueError:
        return False
    rel = _norm(rel)
    for path, _zone in _HARD_WRITE_INVARIANT_PATHS:
        if rel == path:
            return True
    return False


def os_relpath(path, start):
    return os.path.relpath(path, start)
