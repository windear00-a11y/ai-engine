"""Controlled Write/Edit Tools for the Coding Tools Layer.

These tools allow the engine to create and modify files inside an explicitly
configured workspace. They are deliberately the ONLY tools that perform writes;
they reuse the same :class:`tools.coding.fs.Workspace` safety boundary as the
read-only tools, so no path can ever escape the workspace root (this includes
``../`` traversal, absolute paths outside the root, and symlink escapes), and
path validation is never duplicated.

All operations are deterministic and return JSON-serializable ``dict`` results
with a structured ``"error"`` field on failure rather than raising.
"""

import difflib
import os

try:
    from .fs import Workspace, PathError, is_binary
except Exception:  # pragma: no cover - defensive
    from tools.coding.fs import Workspace, PathError, is_binary


class WriteTools:
    def __init__(self, root, permissions=None):
        self.ws = Workspace(root)
        self.permissions = permissions

    # -- permission helper -------------------------------------------------
    def _hard_guard(self, abs_path):
        """Hard invariant: never write the frozen production knowledge DB.

        In addition to any configured gate, this is enforced unconditionally
        so database/knowledge.db (and its backup) can never be written no
        matter what policy is in force.
        """
        try:
            from tools.permissions.pathpolicy import hard_write_guard
            return hard_write_guard(abs_path, self.ws.root)
        except Exception:
            return False

    def _authorize(self, path, abs_path, operation, content=None,
                   multi_file=False):
        """Return (allowed, error). If a permission gate is configured,
        evaluate the write there; otherwise rely on the hard guard."""
        if self._hard_guard(abs_path):
            return (False,
                    "write denied: frozen production database is immutable")
        if self.permissions is not None:
            snapshot_provider = None
            try:
                # For destructive writes capture the current on-disk bytes
                # as the before-snapshot.
                if os.path.isfile(abs_path):
                    def _snap(_p=abs_path):
                        with open(_p, "rb") as _f:
                            return _f.read()
                    snapshot_provider = _snap
            except Exception:
                snapshot_provider = None
            allowed, _decision, err = self.permissions.authorize_write(
                path, operation, content=content,
                snapshot_provider=snapshot_provider, multi_file=multi_file)
            return (allowed, err)
        return (True, None)

    def write(self, path, content, overwrite=True):
        if not isinstance(path, str) or not path:
            return {"path": path, "error": "path must be a non-empty string",
                    "bytes_written": 0, "created_or_updated": None}
        if not isinstance(content, str):
            return {"path": path, "error": "content must be a string",
                    "bytes_written": 0, "created_or_updated": None}
        if not isinstance(overwrite, bool):
            return {"path": path, "error": "overwrite must be a bool",
                    "bytes_written": 0, "created_or_updated": None}
        try:
            abs_path = self.ws.resolve(path)
        except PathError as e:
            return {"path": path, "error": str(e), "bytes_written": 0,
                    "created_or_updated": None}
        if os.path.exists(abs_path) and os.path.isdir(abs_path):
            return {"path": path, "error": "path is a directory",
                    "bytes_written": 0, "created_or_updated": None}
        if os.path.exists(abs_path) and not overwrite:
            return {"path": path,
                    "error": "file already exists; pass overwrite=True to replace",
                    "bytes_written": 0, "created_or_updated": None}

        allowed, err = self._authorize(path, abs_path, "file.write",
                                       content=content)
        if not allowed:
            return {"path": path, "error": err, "bytes_written": 0,
                    "created_or_updated": None}

        parent = os.path.dirname(abs_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)

        existed = os.path.exists(abs_path)
        data = content.encode("utf-8")
        with open(abs_path, "wb") as f:
            f.write(data)

        return {
            "path": path,
            "root": self.ws.root,
            "bytes_written": len(data),
            "created_or_updated": "updated" if existed else "created",
            "error": None,
        }

    def edit(self, path, old_text, new_text, replace_all=False):
        if not isinstance(path, str) or not path:
            return {"path": path, "error": "path must be a non-empty string",
                    "replacements": 0, "previous_size": None, "new_size": None}
        if not isinstance(old_text, str) or old_text == "":
            return {"path": path, "error": "old_text must be a non-empty string",
                    "replacements": 0, "previous_size": None, "new_size": None}
        if not isinstance(new_text, str):
            return {"path": path, "error": "new_text must be a string",
                    "replacements": 0, "previous_size": None, "new_size": None}
        if not isinstance(replace_all, bool):
            return {"path": path, "error": "replace_all must be a bool",
                    "replacements": 0, "previous_size": None, "new_size": None}
        try:
            abs_path = self.ws.resolve(path)
        except PathError as e:
            return {"path": path, "error": str(e), "replacements": 0,
                    "previous_size": None, "new_size": None}
        if not os.path.exists(abs_path):
            return {"path": path, "error": "file not found", "replacements": 0,
                    "previous_size": None, "new_size": None}
        if os.path.isdir(abs_path):
            return {"path": path, "error": "is a directory", "replacements": 0,
                    "previous_size": None, "new_size": None}
        if is_binary(abs_path):
            return {"path": path, "error": "binary file cannot be edited",
                    "replacements": 0, "previous_size": None, "new_size": None}

        allowed, err = self._authorize(path, abs_path, "file.edit",
                                       content=new_text)
        if not allowed:
            return {"path": path, "error": err, "replacements": 0,
                    "previous_size": None, "new_size": None}

        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except OSError as e:
            return {"path": path, "error": str(e), "replacements": 0,
                    "previous_size": None, "new_size": None}

        count = content.count(old_text)
        if count == 0:
            return {"path": path, "error": "old_text not found in file",
                    "replacements": 0, "previous_size": None, "new_size": None}
        if not replace_all and count > 1:
            return {"path": path,
                    "error": f"ambiguous: old_text matches {count} times; "
                             f"pass replace_all=True or provide unique text",
                    "replacements": 0, "previous_size": None, "new_size": None}

        if replace_all:
            new_content = content.replace(old_text, new_text)
            replacements = count
        else:
            new_content = content.replace(old_text, new_text, 1)
            replacements = 1

        previous_size = len(content.encode("utf-8"))
        new_size = len(new_content.encode("utf-8"))

        with open(abs_path, "w", encoding="utf-8") as f:
            f.write(new_content)

        return {
            "path": path,
            "root": self.ws.root,
            "replacements": replacements,
            "previous_size": previous_size,
            "new_size": new_size,
            "error": None,
        }

    def mkdir(self, path, parents=True):
        if not isinstance(path, str) or not path:
            return {"path": path, "error": "path must be a non-empty string",
                    "created": None}
        if not isinstance(parents, bool):
            return {"path": path, "error": "parents must be a bool",
                    "created": None}
        try:
            abs_path = self.ws.resolve(path)
        except PathError as e:
            return {"path": path, "error": str(e), "created": None}
        if os.path.exists(abs_path) and not os.path.isdir(abs_path):
            return {"path": path,
                    "error": "path exists and is not a directory", "created": None}

        allowed, err = self._authorize(path, abs_path, "file.mkdir")
        if not allowed:
            return {"path": path, "error": err, "created": None}

        existed = os.path.isdir(abs_path)
        os.makedirs(abs_path, exist_ok=True)
        return {"path": path, "root": self.ws.root, "created": not existed,
                "error": None}

    def diff(self, path, proposed_content):
        if not isinstance(path, str) or not path:
            return {"path": path, "error": "path must be a non-empty string",
                    "exists": None, "diff": "", "lines_added": 0,
                    "lines_removed": 0, "changed": None}
        if not isinstance(proposed_content, str):
            return {"path": path, "error": "proposed_content must be a string",
                    "exists": None, "diff": "", "lines_added": 0,
                    "lines_removed": 0, "changed": None}
        try:
            abs_path = self.ws.resolve(path)
        except PathError as e:
            return {"path": path, "error": str(e), "exists": None, "diff": "",
                    "lines_added": 0, "lines_removed": 0, "changed": None}
        if os.path.exists(abs_path) and os.path.isdir(abs_path):
            return {"path": path, "error": "is a directory", "exists": None,
                    "diff": "", "lines_added": 0, "lines_removed": 0,
                    "changed": None}
        if os.path.exists(abs_path) and is_binary(abs_path):
            return {"path": path, "error": "binary file", "exists": None,
                    "diff": "", "lines_added": 0, "lines_removed": 0,
                    "changed": None}

        current = ""
        exists = os.path.exists(abs_path)
        if exists:
            try:
                with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                    current = f.read()
            except OSError as e:
                return {"path": path, "error": str(e), "exists": None,
                        "diff": "", "lines_added": 0, "lines_removed": 0,
                        "changed": None}

        a_lines = current.splitlines(keepends=True)
        b_lines = proposed_content.splitlines(keepends=True)
        diff_lines = list(difflib.unified_diff(
            a_lines, b_lines,
            fromfile="a/" + path, tofile="b/" + path, lineterm="",
        ))
        added = sum(1 for l in diff_lines
                    if l.startswith("+") and not l.startswith("+++"))
        removed = sum(1 for l in diff_lines
                      if l.startswith("-") and not l.startswith("---"))

        return {
            "path": path,
            "root": self.ws.root,
            "exists": exists,
            "diff": "\n".join(diff_lines),
            "lines_added": added,
            "lines_removed": removed,
            "changed": current != proposed_content,
            "error": None,
        }
