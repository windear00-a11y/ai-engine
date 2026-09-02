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
    """Namespaced git inspection bound to one workspace root.

    Reads (``status``/``diff``/``log``) are ungated. Writes (``stage``/
    ``commit``) are approval-gated through ``permissions.authorize_git`` and
    double-checked for policy + live approver. Nothing here can push, force,
    amend, rewrite history, touch hooks or config, or escape the workspace.
    """

    def __init__(self, workspace_root, permissions=None):
        self.root = os.path.realpath(workspace_root or ".")
        self.permissions = permissions

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

    # -- approval-gated writes (7B) ---------------------------------------

    def _authorize(self, operation, proposal, message):
        if self.permissions is None:
            return {"ok": False, "error":
                    "git %s requires an approval gate" % operation}
        if not hasattr(self.permissions, "authorize_git"):
            return {"ok": False, "error":
                    "approval gate does not support git authorization"}
        allowed, _d, err = self.permissions.authorize_git(
            operation, proposal)
        if not allowed:
            return {"ok": False, "error": err,
                    "operation": operation, "message": message}

    def stage(self, paths):
        """Approval-gated ``git add -- <paths>``; paths bounded to root."""
        if not isinstance(paths, list) or not paths:
            return {"ok": False, "error":
                    "paths must be a non-empty list"}
        if len(paths) > 100:
            return {"ok": False, "error": "too many paths"}
        abs_paths = []
        for rel in paths:
            if not isinstance(rel, str):
                return {"ok": False, "error":
                        "every path must be a string"}
            abs_path, perr = self._validate_rel_path(rel)
            if perr:
                return {"ok": False, "error": perr}
            abs_paths.append(abs_path)

        gate = self._authorize("stage", {"op": "stage", "paths": paths}, None)
        if gate is not None:
            return gate

        rc, out, err = self._spawn(["add", "--"] + abs_paths)
        if rc is None:
            return {"ok": False, "error": err}
        if rc != 0:
            return {"ok": False, "error": self._describe(err)}
        staged = self.status().get("staged", [])
        return {"ok": True, "op": "stage", "staged": staged,
                "paths": [os.path.relpath(p, self.root) for p in abs_paths],
                "count": len(abs_paths)}

    def commit(self, message):
        """Approval-gated ``git commit -m <message>``; exactly-once."""
        if not isinstance(message, str) or not message.strip():
            return {"ok": False, "error":
                    "message must be a non-empty string"}
        if "\n" in message:
            return {"ok": False, "error":
                    "message must be a single line"}
        if len(message) > 200:
            return {"ok": False, "error":
                    "message must be at most 200 characters"}

        gate = self._authorize("commit", {"op": "commit", "message": message},
                               message)
        if gate is not None:
            return gate

        rc, out, err = self._spawn(["commit", "-m", message])
        if rc is None:
            return {"ok": False, "error": err}
        if rc != 0:
            return {"ok": False, "error": self._describe(err)}
        head = None
        for ln in out.splitlines():
            if " " in ln:
                head = ln.split()[0]
                break
        log = self.log(n=1).get("commits", [])
        if log:
            head = log[0]["hash"]
        return {"ok": True, "op": "commit", "message": message,
                "commit": head, "short": head}

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