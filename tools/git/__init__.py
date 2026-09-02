"""Git inspection tools layer (Layer 7A).

Deterministic, workspace-bounded, READ-ONLY git inspection as first-class
engine tools. A strict closed allowlist of subcommands (no arbitrary shell)
with pre-spawn argument validation, cwd confined to the workspace root via
``git -C <root>``, sanitized environment, and defensive parsing that never
raises: every failure is a structured ``{"ok": False, "error": ...}``.

Blocks writes by construction: only ``status`` / ``diff`` / ``log`` exist,
and no other ``git`` command is reachable from here. ``push`` and every
mutating git operation stay impossible from this layer (Layer 7B owns
approval-gated writes; Layer 7C push stays denied).

Reads require no approval (same posture as ``file.read`` / ``knowledge.get``).
"""

import os
import subprocess

_ALLOWED_COMMANDS = frozenset(("status", "diff", "log"))

_MAX_LOG = 200
_MIN_LOG = 1


class GitTools:
    """Namespaced git inspection bound to one workspace root."""

    def __init__(self, workspace_root):
        self.root = os.path.realpath(workspace_root or ".")

    # -- private plumbing --------------------------------------------------

    def _spawn(self, args, timeout=15):
        """Run an allow-listed, fully-pinned ``git`` invocation. No shell.

        ``args`` excludes ``git -C <root>`` which is prepended here. Returns
        ``(returncode, stdout_text, stderr_text)``; ``None`` returncode means
        the spawn itself failed (never raised to callers).
        """
        cmd = ["git", "-C", self.root] + list(args)
        env = dict(os.environ)
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["LC_ALL"] = "C"
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                env=env, shell=False)
            return (proc.returncode, proc.stdout, proc.stderr)
        except (OSError, subprocess.SubprocessError) as e:
            return (None, "", f"git spawn failed: {e}")

    def _validate_rel_path(self, rel):
        """Return an absolute in-root path, or an error string."""
        if not isinstance(rel, str) or not rel:
            return ("", "path must be a non-empty string")
        if os.path.isabs(rel):
            return ("", "path must be workspace-relative")
        if rel.startswith("..") or "/.." in "/" + rel:
            return ("", "path escapes the workspace")
        abs_path = os.path.realpath(os.path.join(self.root, rel))
        if abs_path != self.root and not abs_path.startswith(
                os.path.join(self.root, "")):
            return ("", "path escapes the workspace")
        if not os.path.exists(abs_path):
            return ("", "no such path")
        return (abs_path, None)

    def _not_ok(self, error):
        return {"ok": False, "error": error}

    # -- public tools -----------------------------------------------------

    def status(self):
        """``git status --porcelain`` parsed into deterministic classes."""
        rc, out, err = self._spawn(["status", "--porcelain"])
        if rc is None:
            return self._not_ok(err)
        if rc != 0:
            return self._not_ok(self._describe(err))
        untracked, staged, modified, deleted, renamed = [], [], [], [], []
        raw = [ln for ln in out.splitlines() if ln]
        for ln in raw:
            xy = ln[:2]
            path = ln[3:] if len(ln) > 3 else ""
            if xy == "??":
                untracked.append(path)
            elif "R" in xy:
                renamed.append(path)
            elif "D" in xy:
                deleted.append(path)
            elif xy[0] in "MA":
                staged.append(path)
            elif "M" in xy:
                modified.append(path)
        return {
            "ok": True,
            "repo": self.root,
            "clean": not raw,
            "untracked": sorted(untracked),
            "staged": sorted(staged),
            "modified": sorted(modified),
            "deleted": sorted(deleted),
            "renamed": sorted(renamed),
            "count": len(raw),
            "raw": raw,
        }

    def diff(self, path=None):
        """``git diff --no-ext-diff [-- REL]``; optional in-root relative path."""
        if path is not None:
            abs_path, perr = self._validate_rel_path(path)
            if perr:
                return self._not_ok(perr)
            args = ["diff", "--no-ext-diff", "--", abs_path]
        else:
            args = ["diff", "--no-ext-diff"]
        rc, out, err = self._spawn(args)
        if rc is None:
            return self._not_ok(err)
        if rc not in (0, 1):
            # git diff exits 1 when diffs exist; anything else is an error.
            return self._not_ok(self._describe(err))
        lines = out.splitlines()
        files_changed = []
        for ln in lines:
            if ln.startswith("diff --git "):
                tail = ln[len("diff --git "):]
                b = tail.split(" b/", 1)
                files_changed.append(b[1] if len(b) == 2
                                     else tail.lstrip("a/"))
        return {
            "ok": True,
            "repo": self.root,
            "path": path,
            "changed": bool(lines),
            "files_changed": sorted(
                dict.fromkeys(files_changed)),
            "count": len(files_changed),
            "raw": lines,
        }

    def log(self, n=20):
        """``git log --oneline -n N``; N in 1..200 (clamped down)."""
        if isinstance(n, bool) or not isinstance(n, int):
            return self._not_ok("n must be an integer")
        if n < _MIN_LOG:
            return self._not_ok("n must be a positive integer")
        n = max(_MIN_LOG, min(_MAX_LOG, n))
        rc, out, err = self._spawn(["log", "--oneline", "-n", str(n)])
        if rc is None:
            return self._not_ok(err)
        if rc != 0:
            return self._not_ok(self._describe(err))
        commits = []
        for ln in out.splitlines():
            if not ln:
                continue
            head, _, subject = ln.partition(" ")
            commits.append({"hash": head, "subject": subject})
        return {"ok": True, "repo": self.root, "commits": commits,
                "count": len(commits)}

    # -- safety net -------------------------------------------------------

    def _describe(self, stderr):
        stderr = (stderr or "").strip()
        return stderr or "git command failed"

    def __getattr__(self, name):
        # Any tool name outside the allowlist is a hard error, never a channel
        # to arbitrary git execution.
        if name.startswith("_"):
            raise AttributeError(name)

        def _closed(*_args, **kwargs):
            return {"ok": False,
                    "error": f"git.{name} is not an allowed read tool"}
        return _closed