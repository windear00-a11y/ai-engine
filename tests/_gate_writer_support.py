"""Shared test support: a minimal Core-only write orchestrator.

The ``WriteTools`` coding facade is removed in Phase 24. This class replicates
its write/edit orchestration using ONLY the generic trust primitives
(``hard_write_guard`` + the approval gate's write flow) so the trust
regressions stay meaningful.

Not discovered by the batch runner (filename does not start with ``test_``).
"""

import os

from tools.permissions import checksum_bytes
from tools.permissions.pathpolicy import hard_write_guard


class GateWriter:
    """Faithful Core-only re-implementation of the write orchestration."""

    def __init__(self, root, permissions):
        self.root = root
        self.permissions = permissions

    def _resolve(self, path):
        return self.permissions.path_policy.resolve(path)

    def _auth(self, path, abs_path, operation, content=None, group_id=None):
        if hard_write_guard(abs_path, self.root):
            return (False,
                    "write denied: frozen production database is immutable",
                    None)
        if self.permissions is None:
            return (True, None, None)
        snapshot_provider = None
        try:
            if os.path.isfile(abs_path):
                def _snap(_p=abs_path):
                    with open(_p, "rb") as _f:
                        return _f.read()
                snapshot_provider = _snap
        except Exception:
            snapshot_provider = None
        allowed, _d, err, info = \
            self.permissions.authorize_write_detailed(
                path, operation, content=content,
                snapshot_provider=snapshot_provider,
                multi_file=bool(group_id), group_id=group_id)
        return (allowed, err, info)

    def _after_write(self, info, abs_path, after_checksum=None):
        try:
            op_id = info.get("operation_id")
            target_rel = info.get("target_rel")
            if after_checksum is not None:
                self.permissions.record_after(op_id, target_rel,
                                              after_checksum)
            with open(abs_path, "rb") as f:
                ok = checksum_bytes(f.read()) == after_checksum
            if ok:
                self.permissions.complete_operation(op_id)
            else:
                self.permissions.fail_operation(op_id)
            return ok
        except Exception:
            return False

    def write(self, path, content, overwrite=True, group_id=None):
        try:
            abs_path = self._resolve(path)
        except Exception as e:
            return {"path": path, "error": str(e), "bytes_written": 0,
                    "created_or_updated": None}
        if os.path.exists(abs_path) and os.path.isdir(abs_path):
            return {"path": path, "error": "path is a directory",
                    "bytes_written": 0, "created_or_updated": None}
        if os.path.exists(abs_path) and not overwrite:
            return {"path": path,
                    "error": "file already exists; pass overwrite=True to replace",
                    "bytes_written": 0, "created_or_updated": None}

        allowed, err, info = self._auth(path, abs_path, "file.write",
                                        content=content, group_id=group_id)
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

        self._after_write(info, abs_path, after_checksum=checksum_bytes(data))

        return {
            "path": path, "root": self.root, "bytes_written": len(data),
            "created_or_updated": "updated" if existed else "created",
            "operation_id": (info or {}).get("operation_id"), "error": None,
        }

    def edit(self, path, old_text, new_text, replace_all=False, group_id=None):
        try:
            abs_path = self._resolve(path)
        except Exception as e:
            return {"path": path, "error": str(e), "replacements": 0,
                    "previous_size": None, "new_size": None}
        if not os.path.exists(abs_path):
            return {"path": path, "error": "file not found",
                    "replacements": 0, "previous_size": None,
                    "new_size": None}
        if os.path.isdir(abs_path):
            return {"path": path, "error": "is a directory",
                    "replacements": 0, "previous_size": None,
                    "new_size": None}

        allowed, err, info = self._auth(path, abs_path, "file.edit",
                                        content=new_text, group_id=group_id)
        if not allowed:
            return {"path": path, "error": err, "replacements": 0,
                    "previous_size": None, "new_size": None}

        with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        count = content.count(old_text)
        if count == 0:
            return {"path": path, "error": "old_text not found in file",
                    "replacements": 0, "previous_size": None,
                    "new_size": None}
        if not replace_all and count > 1:
            return {"path": path,
                    "error": f"ambiguous: old_text matches {count} times; "
                             f"pass replace_all=True or provide unique text",
                    "replacements": 0, "previous_size": None,
                    "new_size": None}

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

        self._after_write(info, abs_path,
                          after_checksum=checksum_bytes(
                              new_content.encode("utf-8")))

        return {
            "path": path, "root": self.root, "replacements": replacements,
            "previous_size": previous_size, "new_size": new_size,
            "operation_id": (info or {}).get("operation_id"), "error": None,
        }