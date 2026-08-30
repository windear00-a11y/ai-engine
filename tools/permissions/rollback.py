"""Journal-based rollback executor (Phase 1B).

Reverts an approved, executed write operation using its before-images:
* existing files are restored to their exact pre-write bytes,
* brand-new files this operation created are removed (and only those),
* operations whose files were externally modified after the write are NEVER
  silently overwritten: they produce a ``rollback_conflict`` requiring an
  explicit ``rollback.confirm`` (decision 7/8/9).

Rollback goes through the same permission/path safety layer as writes
(``PathPolicy`` resolve + ``hard_write_guard``); blocked/read-only paths can
never be restored, with or without confirmation.

Multi-file operations pre-scan ALL files for conflicts before restoring any
(all-or-nothing pre-check). True filesystem-level atomic multi-file rollback
is explicitly out of scope (decision 10); crash recovery is deferred
(decision 11).

This module is deterministic and performs no AI/LLM work.
"""

import os

from tools.permissions.journal import checksum_bytes
from tools.permissions.pathpolicy import hard_write_guard


class RollbackExecutor:
    """Restores filesystem state from journal before-images.

    Parameters
    ----------
    path_policy : PathPolicy
        Single source of truth for workspace path resolution + health zones.
    state : EngineState
        Holds the journal and operation state machine.
    """

    def __init__(self, path_policy, state):
        self.pp = path_policy
        self.state = state

    # -- resolution ---------------------------------------------------------

    def _resolve(self, rel):
        """(abs_path, mode, reason) using the workspace/path safety layer."""
        try:
            abs_path = self.pp.resolve(rel)
        except Exception as e:
            return (None, "deny", "outside_workspace")
        if hard_write_guard(abs_path, self.pp.root):
            return (abs_path, "deny", "blocked")
        info = self.pp.mode_for(abs_path)
        return (abs_path, info["mode"], info.get("reason_code"))

    def resolve_fn(self, rel):
        """Callable the approval gate uses to scope every rollback target."""
        abs_path, mode, _reason = self._resolve(rel)
        return (abs_path, mode)

    # -- plan (pure read) ---------------------------------------------------

    def plan(self, operation_id):
        """Analyze every journal row for an operation WITHOUT touching disk.

        Returns
        -------
        dict with keys ``ok``, ``not_found``, ``not_owned``, ``rows``,
        ``conflicts``, ``safe``, ``summary``.
        Per-row fields: ``seq, target_rel, existed_before, action, safe,
        conflict``. ``action`` is one of ``restore`` (put the before-image
        back), ``delete`` (remove a file this op created), ``skip`` (nothing
        to undo), or ``restore_pending``/``delete_pending`` (conflict).
        """
        op = self.state.get_operation(operation_id)
        if op is None:
            return {"ok": False, "not_found": True, "not_owned": False,
                    "rows": [], "conflicts": [], "safe": False,
                    "summary": "unknown operation"}
        if op["domain"] != "write":
            return {"ok": False, "not_found": False, "not_owned": True,
                    "rows": [], "conflicts": [], "safe": False,
                    "summary": "operation is not a workspace write"}

        rows_raw = self.state.list_for_operation(operation_id)
        conflicts = []
        rows = []
        safe = True

        for r in rows_raw:
            row = dict(r)
            rel = row["target_rel"]
            abs_path, mode, reason = self._resolve(rel)

            if mode in ("deny", "read_only"):
                row.update({"action": "blocked", "safe": False,
                            "conflict": reason or "blocked",
                            "abs_path": abs_path})
                safe = False
                conflicts.append({"target_rel": rel,
                                  "reason": row["conflict"]})
                rows.append(row)
                continue

            after = row.get("after_checksum")
            existed_before = bool(row.get("existed_before"))

            if not after:
                # No post-write checksum recorded -> the write never completed
                # (or never ran). Nothing to undo.
                row.update({"action": "skip", "safe": True,
                            "conflict": None, "abs_path": abs_path})
                rows.append(row)
                continue

            row["abs_path"] = abs_path
            if not existed_before:
                # A file this operation created. Desired rollback state is
                # "absent". Only delete when the current bytes still match
                # what we wrote (TOCTOU-safe).
                if not os.path.exists(abs_path):
                    row.update({"action": "skip", "safe": True,
                                "conflict": None})
                else:
                    try:
                        current = checksum_bytes(_read_bytes(abs_path))
                    except Exception:
                        current = None
                    if current == after:
                        row.update({"action": "delete", "safe": True,
                                    "conflict": None})
                    else:
                        row.update({"action": "delete_pending", "safe": False,
                                    "conflict": "toctou"})
                        safe = False
                        conflicts.append({"target_rel": rel,
                                          "reason": "toctou"})
            else:
                # Existing file with a before-image: restore only if our write
                # is still the current content (otherwise externally modified).
                has_snapshot = (row.get("content") is not None
                                or row.get("snapshot_path"))
                row.update({"action": "restore", "safe": True,
                            "conflict": None})
                if not has_snapshot:
                    row.update({"action": "restore_pending", "safe": False,
                                "conflict": "no_snapshot"})
                    safe = False
                    conflicts.append({"target_rel": rel,
                                      "reason": "no_snapshot"})
                elif not os.path.exists(abs_path):
                    row.update({"action": "restore_pending", "safe": False,
                                "conflict": "toctou"})
                    safe = False
                    conflicts.append({"target_rel": rel, "reason": "toctou"})
                else:
                    try:
                        current = checksum_bytes(_read_bytes(abs_path))
                    except Exception:
                        current = None
                    if current != after:
                        row.update({"action": "restore_pending",
                                    "safe": False, "conflict": "toctou"})
                        safe = False
                        conflicts.append({"target_rel": rel,
                                          "reason": "toctou"})
            rows.append(row)

        return {
            "ok": True, "not_found": False, "not_owned": False,
            "rows": rows, "conflicts": conflicts, "safe": safe,
            "summary": ("ready" if safe else "conflicts"),
            "op_status": op["status"],
        }

    # -- execute ------------------------------------------------------------

    def execute(self, operation_id, confirm=False):
        """Restore an operation (all-or-nothing pre-scan first).

        ``confirm`` is a flag from the tool layer meaning the approval gate
        already authorized an explicit-confirm rollback. Even then, blocked
        paths are never restored, and a TOCTOU mismatch found during the final
        pre-check aborts the whole restore.
        """
        p = self.plan(operation_id)
        if not p["ok"]:
            return {"result": "rollback_failed", "operation_id": operation_id,
                    "error": p["summary"], "restored": [], "deleted": [],
                    "conflicts": p["conflicts"]}
        if not confirm and not p["safe"]:
            self.state.set_operation_status(operation_id,
                                            "rollback_conflict")
            return {"result": "rollback_conflict",
                    "operation_id": operation_id,
                    "error": "external modification detected; "
                             "rollback.confirm is required",
                    "restored": [], "deleted": [],
                    "conflicts": p["conflicts"]}

        # ALL files are re-verified against their after_checksum before ANY
        # restore begins (decision 9: pre-scan every file, restore nothing on
        # a single mismatch). A confirmed rollback may override TOCTOU
        # mismatches (the approver explicitly allowed restoring them).
        to_restore = []
        to_delete = []
        served = {}
        mismatches = []
        for row in p["rows"]:
            action = row["action"]
            if action in ("skip", "blocked"):
                served[row["target_rel"]] = {"action": action,
                                             "reason": row.get("conflict")}
                continue
            if action == "restore":
                after = row.get("after_checksum")
                try:
                    current = checksum_bytes(_read_bytes(row["abs_path"]))
                except Exception:
                    current = None
                if current != after:
                    mismatches.append(row["target_rel"])
                    continue
                to_restore.append(row)
            elif action == "delete":
                after = row.get("after_checksum")
                try:
                    current = checksum_bytes(_read_bytes(row["abs_path"]))
                except Exception:
                    current = None
                if current != after:
                    mismatches.append(row["target_rel"])
                    continue
                to_delete.append(row)
            elif action == "restore_pending":
                if not confirm:
                    mismatches.append(row["target_rel"])
                    continue
                to_restore.append(row)
            elif action == "delete_pending":
                if not confirm:
                    mismatches.append(row["target_rel"])
                    continue
                to_delete.append(row)

        if mismatches and not confirm:
            self.state.set_operation_status(operation_id,
                                            "rollback_conflict")
            conflicts = [{"target_rel": m, "reason": "toctou"}
                         for m in mismatches]
            return {"result": "rollback_conflict",
                    "operation_id": operation_id,
                    "error": "external modification detected; "
                             "rollback.confirm is required",
                    "restored": [], "deleted": [], "conflicts": conflicts}

        self.state.set_operation_status(operation_id, "rolling_back")

        restored = []
        deleted = []
        notes = []
        failed = []
        try:
            for row in to_restore:
                has_snapshot = (row.get("content") is not None
                                or row.get("snapshot_path"))
                if not has_snapshot and row.get("existed_before"):
                    failed.append({"target_rel": row["target_rel"],
                                   "reason": "no_snapshot"})
                    continue
                before = self.state.snapshot_bytes(row)
                _atomic_replace(row["abs_path"], before)
                row_seq = row["seq"]
                self.state.set_journal_status(row_seq, "rolled_back")
                restored.append(row["target_rel"])
            for row in to_delete:
                _atomic_remove(row["abs_path"])
                self.state.set_journal_status(row["seq"], "rolled_back")
                deleted.append(row["target_rel"])
        except OSError as e:
            self.state.set_operation_status(operation_id, "rollback_failed")
            return {"result": "rollback_failed",
                    "operation_id": operation_id,
                    "error": f"rollback I/O failure: {e}",
                    "restored": restored, "deleted": deleted,
                    "conflicts": []}
        except Exception as e:  # pragma: no cover - defensive
            self.state.set_operation_status(operation_id, "rollback_failed")
            return {"result": "rollback_failed",
                    "operation_id": operation_id,
                    "error": f"rollback failure: {e}",
                    "restored": restored, "deleted": deleted,
                    "conflicts": []}

        # Verify the rollback actually happened (deterministic check).
        verify_ok = True
        if failed:
            verify_ok = False
        for row in to_restore:
            try:
                if checksum_bytes(_read_bytes(row["abs_path"])) != \
                        row["checksum"]:
                    verify_ok = False
                    break
            except Exception:
                verify_ok = False
                break
        for row in to_delete:
            if os.path.exists(row["abs_path"]):
                verify_ok = False
                break

        for rel, s in served.items():
            if s["action"] == "blocked":
                notes.append({"target_rel": rel, "note":
                              "path not writable; left untouched",
                              "reason": s["reason"]})

        if not verify_ok:
            self.state.set_operation_status(operation_id, "rollback_failed")
            return {"result": "rollback_failed",
                    "operation_id": operation_id,
                    "error": "post-rollback verification failed",
                    "restored": restored, "deleted": deleted,
                    "conflicts": [], "notes": notes, "failed": failed}

        self.state.set_operation_status(operation_id, "rolled_back")
        return {"result": "rolled_back", "operation_id": operation_id,
                "error": None, "restored": restored, "deleted": deleted,
                "conflicts": [], "notes": notes}


def _read_bytes(abs_path):
    with open(abs_path, "rb") as f:
        return f.read()


def _atomic_replace(abs_path, data):
    """Write ``data`` to a temp file in the same directory, then replace."""
    directory = os.path.dirname(abs_path)
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    tmp = abs_path + ".rollback-tmp"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, abs_path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _atomic_remove(abs_path):
    tmp = abs_path + ".rollback-tmp"
    try:
        os.replace(abs_path, tmp)
    except OSError:
        os.remove(abs_path)
        return
    os.remove(tmp)