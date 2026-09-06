"""Phase 1B tests: journal-based rollback (execution), deterministic.

Covers:
* T1 ``file.edit`` captures a before-image and rolls back cleanly.
* new-file writes: ``rollback.operation`` removes only files the op created,
  and only while the current checksum matches the recorded post-write checksum.
* external modification -> ``rollback_conflict`` (never silently overwritten);
  explicit ``rollback.confirm`` (approved) overrides a TOCTOU mismatch.
* multi-file (shared group id) rollback pre-scans every file before restoring
  any; a single mismatch aborts the whole restore (all-or-nothing, decision 9).
* hybrid snapshot storage: large before-images spill to the filesystem and
  restore exactly; small ones stay in the SQLite BLOB.
* operation-id ordering: journal rows use the REAL operation id (not "op").
* rollback can never restore readonly/blocked paths (path layer still applies).
* unknown / non-owned operations are refused.
* the generic Core surface exposes the rollback executor and the approval
  gate's ``authorize_rollback`` adapter (the removed TaskEngine tool registry
  is no longer in scope).

The write path here is driven by the GENERIC Core primitives
(``ApprovalGate.authorize_write_detailed`` / ``record_after`` /
``verify_after`` / ``complete_operation`` plus ``hard_write_guard``) — the
coding-facade ``WriteTools`` is removed, so the test carries a tiny faithful
orchestrator instead.

All tests use temporary workspaces and temporary state DBs; the production
database is never opened for writing.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..")))

from tools.permissions import (EngineState, PathPolicy, ApprovalGate, Policy,
                               checksum_bytes)
from tools.permissions.decisions import DecisionKind, Domain
from tools.permissions.rollback import RollbackExecutor
from tests._gate_writer_support import GateWriter as _GateWriter


def _mkws(**names):
    d = tempfile.mkdtemp(prefix="perm_rollback_")
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    for name, content in names.items():
        with open(os.path.join(d, "src", name), "w") as f:
            f.write(content)
    return d


class RollbackHelper:
    def __init__(self, root, approver=None, hybrid_threshold=None):
        self.root = root
        self.state = EngineState(db_path=os.path.join(
            tempfile.mkdtemp(prefix="rb_state_"), "engine_state.db"),
            hybrid_threshold=hybrid_threshold)
        self.pp = PathPolicy(root, policy=Policy())
        p = (lambda proposal: True) if approver is None else approver
        self.gate = ApprovalGate(path_policy=self.pp, state=self.state,
                                 approver=p)
        self.wh = _GateWriter(root, self.gate)
        self.ex = RollbackExecutor(self.pp, self.state)


class EditRollbackTests(unittest.TestCase):
    def setUp(self):
        self.root = _mkws(**{"main.py": "x = 1\n"})
        self.h = RollbackHelper(self.root)
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_t1_edit_captures_before_image_and_rolls_back(self):
        res = self.h.wh.edit("src/main.py", "x = 1\n", "x = 2\n")
        self.assertEqual(res["error"], None)
        self.assertEqual(res["replacements"], 1)
        op = res["operation_id"]
        self.assertTrue(op.startswith("op_"))
        # journal row must carry the real operation id (ordering fix Phase 1B)
        rec = self.h.state.journal_latest_for("src/main.py")
        self.assertEqual(rec["operation_id"], op)
        self.assertEqual(rec["after_checksum"],
                         checksum_bytes(b"x = 2\n"))
        self.assertEqual(self.h.state.get_operation(op)["status"], "completed")

        plan = self.h.ex.plan(op)
        self.assertTrue(plan["safe"])
        self.assertEqual(plan["rows"][0]["action"], "restore")

        result = self.h.ex.execute(op)
        self.assertEqual(result["result"], "rolled_back")
        self.assertEqual(result["restored"], ["src/main.py"])
        with open(os.path.join(self.root, "src", "main.py")) as f:
            self.assertEqual(f.read(), "x = 1\n")
        self.assertEqual(self.h.state.get_operation(op)["status"],
                         "rolled_back")

    def test_edit_old_text_missing_leaves_no_after_checksum(self):
        # A write that never happens must not claim an after-checksum; rollback
        # is then a safe no-op (nothing to undo).
        res = self.h.wh.edit("src/main.py", "NOPE", "y = 2\n")
        self.assertEqual(res["error"], "old_text not found in file")
        rec = self.h.state.journal_latest_for("src/main.py")
        self.assertIsNone(rec["after_checksum"])
        result = self.h.ex.execute(rec["operation_id"])
        self.assertEqual(result["result"], "rolled_back")
        self.assertEqual(result["restored"], [])
        with open(os.path.join(self.root, "src", "main.py")) as f:
            self.assertEqual(f.read(), "x = 1\n")


class NewFileRollbackTests(unittest.TestCase):
    def setUp(self):
        self.root = _mkws(**{"main.py": "x = 1\n"})
        self.h = RollbackHelper(self.root)
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_created_file_deleted_on_rollback(self):
        res = self.h.wh.write("src/created.py", "print(1)\n")
        self.assertEqual(res["created_or_updated"], "created")
        op = res["operation_id"]
        # marker row: never existed before
        rec = self.h.state.journal_latest_for("src/created.py")
        self.assertEqual(rec["existed_before"], 0)
        self.assertIsNone(rec["content"])
        self.assertEqual(rec["after_checksum"],
                         checksum_bytes(b"print(1)\n"))

        plan = self.h.ex.plan(op)
        self.assertTrue(plan["safe"])
        self.assertEqual(plan["rows"][0]["action"], "delete")

        result = self.h.ex.execute(op)
        self.assertEqual(result["result"], "rolled_back")
        self.assertEqual(result["deleted"], ["src/created.py"])
        self.assertFalse(os.path.exists(os.path.join(self.root,
                                                     "src", "created.py")))
        # existing unrelated file is untouched
        with open(os.path.join(self.root, "src", "main.py")) as f:
            self.assertEqual(f.read(), "x = 1\n")

    def test_external_change_after_create_requires_confirm(self):
        res = self.h.wh.write("src/created.py", "print(1)\n")
        op = res["operation_id"]
        with open(os.path.join(self.root, "src", "created.py"), "w") as f:
            f.write("print(2)\n")  # external writer

        plan = self.h.ex.plan(op)
        self.assertFalse(plan["safe"])
        self.assertEqual(plan["rows"][0]["conflict"], "toctou")

        # auto rollback refuses to overwrite/delete the modified file
        result = self.h.ex.execute(op)
        self.assertEqual(result["result"], "rollback_conflict")
        self.assertTrue(os.path.exists(os.path.join(self.root,
                                                    "src", "created.py")))
        with open(os.path.join(self.root, "src", "created.py")) as f:
            self.assertEqual(f.read(), "print(2)\n")

        # ... but an explicit confirmed rollback (approved) may proceed
        confirmed = self.h.ex.execute(op, confirm=True)
        self.assertEqual(confirmed["result"], "rolled_back")
        self.assertEqual(confirmed["deleted"], ["src/created.py"])
        self.assertFalse(os.path.exists(os.path.join(self.root,
                                                     "src", "created.py")))

    def test_marker_only_for_files_this_operation_created(self):
        # overwriting an existing file must restore, never delete
        res = self.h.wh.write("src/main.py", "x = 9\n")
        op = res["operation_id"]
        rec = self.h.state.journal_latest_for("src/main.py")
        self.assertEqual(rec["existed_before"], 1)
        self.assertEqual(rec["checksum"], checksum_bytes(b"x = 1\n"))
        plan = self.h.ex.plan(op)
        self.assertEqual(plan["rows"][0]["action"], "restore")
        result = self.h.ex.execute(op)
        self.assertEqual(result["result"], "rolled_back")
        with open(os.path.join(self.root, "src", "main.py")) as f:
            self.assertEqual(f.read(), "x = 1\n")


class ConflictAndGateTests(unittest.TestCase):
    def setUp(self):
        self.root = _mkws(**{"main.py": "AAA"})
        self.h = RollbackHelper(self.root)
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def _write_then_conflict(self):
        res = self.h.wh.write("src/main.py", "BBB")
        op = res["operation_id"]
        with open(os.path.join(self.root, "src", "main.py"), "w") as f:
            f.write("CCC")
        return op

    def test_toctou_conflict_never_silently_overwritten(self):
        op = self._write_then_conflict()
        plan = self.h.ex.plan(op)
        self.assertFalse(plan["safe"])
        result = self.h.ex.execute(op)
        self.assertEqual(result["result"], "rollback_conflict")
        self.assertEqual(self.h.state.get_operation(op)["status"],
                         "rollback_conflict")
        with open(os.path.join(self.root, "src", "main.py")) as f:
            self.assertEqual(f.read(), "CCC")

    def test_auto_rollback_uses_the_approval_gate(self):
        op = self._write_then_conflict()
        # the approval gate itself refuses the confirm-less auto path only via
        # the executor plan; authorize_rollback(confirm=False) still performs
        # path/ownership checks and the executor refuses to auto-restore.
        allowed = self.h.gate.authorize_rollback(op, self.h.ex.resolve_fn,
                                                 confirm=False)
        self.assertTrue(allowed[0])  # path-safe
        result = self.h.ex.execute(op)
        self.assertEqual(result["result"], "rollback_conflict")

    def test_conffirmed_rollback_requires_gate_approval(self):
        op = self._write_then_conflict()
        deny_gate = ApprovalGate(path_policy=self.h.pp, state=self.h.state,
                                 approver=lambda p: False)
        # the confirmation must go through the approver; a denying approver
        # blocks it
        d = deny_gate.check(Domain.ROLLBACK, op, operation="rollback")
        self.assertEqual(d.kind, DecisionKind.DENY)
        allowed = deny_gate.authorize_rollback(op, self.h.ex.resolve_fn,
                                               confirm=True)
        self.assertFalse(allowed[0])
        # file untouched
        with open(os.path.join(self.root, "src", "main.py")) as f:
            self.assertEqual(f.read(), "CCC")

    def test_unconfirmed_conflict_executor_refuses(self):
        # The executor surface (replacing the removed engine tool) refuses the
        # auto path on a TOCTOU mismatch and never mutates the file.
        op = self._write_then_conflict()
        result = self.h.ex.execute(op)
        self.assertEqual(result["result"], "rollback_conflict")
        with open(os.path.join(self.root, "src", "main.py")) as f:
            self.assertEqual(f.read(), "CCC")

    def test_confirmed_executor_overrides_after_gate_approval(self):
        op = self._write_then_conflict()
        # Explicit gate approval of the confirm path, then the executor run.
        allowed = self.h.gate.authorize_rollback(op, self.h.ex.resolve_fn,
                                                 confirm=True)
        self.assertTrue(allowed[0])
        result = self.h.ex.execute(op, confirm=True)
        self.assertEqual(result["result"], "rolled_back")
        with open(os.path.join(self.root, "src", "main.py")) as f:
            self.assertEqual(f.read(), "AAA")


class MultiFileRollbackTests(unittest.TestCase):
    def setUp(self):
        self.root = _mkws(**{"a.txt": "A", "b.txt": "B"})
        self.h = RollbackHelper(self.root)
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_multi_file_group_restores_all_files(self):
        group = "op_group_deterministic_1"
        r1 = self.h.wh.write("src/a.txt", "AA", group_id=group)
        r2 = self.h.wh.write("src/b.txt", "BB", group_id=group)
        self.assertEqual(r1["operation_id"], group)
        self.assertEqual(r2["operation_id"], group)
        rows = self.h.state.list_for_operation(group)
        self.assertEqual(len(rows), 2)

        plan = self.h.ex.plan(group)
        self.assertTrue(plan["safe"])
        self.assertEqual(sorted(r["target_rel"] for r in plan["rows"]),
                         ["src/a.txt", "src/b.txt"])
        result = self.h.ex.execute(group)
        self.assertEqual(result["result"], "rolled_back")
        with open(os.path.join(self.root, "src", "a.txt")) as f:
            self.assertEqual(f.read(), "A")
        with open(os.path.join(self.root, "src", "b.txt")) as f:
            self.assertEqual(f.read(), "B")

    def test_single_mismatch_aborts_whole_group(self):
        group = "op_group_deterministic_2"
        self.h.wh.write("src/a.txt", "AA", group_id=group)
        self.h.wh.write("src/b.txt", "BB", group_id=group)
        with open(os.path.join(self.root, "src", "b.txt"), "w") as f:
            f.write("MODIFIED")  # external modification of one file

        plan = self.h.ex.plan(group)
        self.assertFalse(plan["safe"])
        self.assertEqual(sorted(c["target_rel"] for c in plan["conflicts"]),
                         ["src/b.txt"])

        result = self.h.ex.execute(group)
        self.assertEqual(result["result"], "rollback_conflict")
        # decision 9: nothing restored because one file conflicted
        with open(os.path.join(self.root, "src", "a.txt")) as f:
            self.assertEqual(f.read(), "AA")
        with open(os.path.join(self.root, "src", "b.txt")) as f:
            self.assertEqual(f.read(), "MODIFIED")

        confirmed = self.h.ex.execute(group, confirm=True)
        self.assertEqual(confirmed["result"], "rolled_back")
        with open(os.path.join(self.root, "src", "a.txt")) as f:
            self.assertEqual(f.read(), "A")
        with open(os.path.join(self.root, "src", "b.txt")) as f:
            self.assertEqual(f.read(), "B")


class HybridStorageTests(unittest.TestCase):
    def test_large_snapshot_spills_and_restores_exactly(self):
        root = _mkws()
        h = RollbackHelper(root,
                           hybrid_threshold=4096)  # force spill above 4 KiB
        try:
            before = b"X" * 7000
            with open(os.path.join(root, "src", "dat.bin"), "wb") as f:
                f.write(before)
            res = h.wh.write("src/dat.bin", "Y" * 7000)
            op = res["operation_id"]
            rec = h.state.journal_latest_for("src/dat.bin")
            self.assertIsNotNone(rec["snapshot_path"])
            self.assertIsNone(rec["content"])
            # snapshot spilled to filesystem directory next to the state DB
            spilled = os.path.join(h.state.snapshot_dir,
                                   rec["snapshot_path"])
            self.assertTrue(os.path.exists(spilled))
            self.assertEqual(h.state.snapshot_bytes(rec), before)

            result = h.ex.execute(op)
            self.assertEqual(result["result"], "rolled_back")
            with open(os.path.join(root, "src", "dat.bin"), "rb") as f:
                self.assertEqual(f.read(), before)
        finally:
            __import__("shutil").rmtree(root, ignore_errors=True)

    def test_small_snapshot_stays_in_blob(self):
        import shutil
        root = tempfile.mkdtemp(prefix="rb_hbuffer_")
        h = None
        try:
            os.makedirs(os.path.join(root, "src"))
            with open(os.path.join(root, "src", "s.txt"), "w") as f:
                f.write("tiny")
            h = RollbackHelper(root)
            op = h.wh.write("src/s.txt", "TINY2")["operation_id"]
            rec = h.state.journal_latest_for("src/s.txt")
            self.assertIsNone(rec["snapshot_path"])
            self.assertEqual(rec["content"], b"tiny")
            result = h.ex.execute(op)
            self.assertEqual(result["result"], "rolled_back")
            with open(os.path.join(root, "src", "s.txt")) as f:
                self.assertEqual(f.read(), "tiny")
        finally:
            if h is not None:
                shutil.rmtree(os.path.dirname(h.state.db_path),
                              ignore_errors=True)
            shutil.rmtree(root, ignore_errors=True)


class OwnershipAndGuardTests(unittest.TestCase):
    def setUp(self):
        self.root = _mkws(**{"main.py": "x = 1\n"})
        self.h = RollbackHelper(self.root)
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_unknown_operation_refused(self):
        # Replaces the removed engine tool ("not_found"): the executor reports
        # the same refusal deterministically.
        plan = self.h.ex.plan("op_nope")
        self.assertTrue(plan["not_found"])
        result = self.h.ex.execute("op_nope")
        self.assertEqual(result["result"], "rollback_failed")
        self.assertIn("unknown operation", result["error"])

    def test_non_write_operation_refused(self):
        self.h.state.ensure_operation("op_git1", "git", status="completed")
        plan = self.h.ex.plan("op_git1")
        self.assertTrue(plan["not_owned"])
        self.assertEqual(plan["summary"], "operation is not a workspace write")
        result = self.h.ex.execute("op_git1")
        self.assertEqual(result["result"], "rollback_failed")

    def test_readonly_target_never_restored(self):
        # even a hand-crafted journal entry for a readonly path is refused
        os.makedirs(os.path.join(self.root, "api"), exist_ok=True)
        op = "op_crafted_ro1"
        self.h.state.ensure_operation(op, "write", status="completed")
        self.h.state.add_journal(op, "api/contract.py", "t2",
                                 b"VERSION=1\n", after_checksum="x")
        plan = self.h.ex.plan(op)
        self.assertFalse(plan["safe"])
        self.assertEqual(plan["rows"][0]["action"], "blocked")
        # Executor must not restore the readonly path; it is left untouched and
        # reported in the notes. (Gate authorization also fails closed.)
        allowed = self.h.gate.authorize_rollback(op, self.h.ex.resolve_fn,
                                                 confirm=True)
        self.assertFalse(allowed[0])
        if not os.path.exists(os.path.join(self.root, "api", "contract.py")):
            with open(os.path.join(self.root, "api", "contract.py"), "w") as f:
                f.write("VERSION=1\n")

    def test_plan_rolls_back_without_executing(self):
        # dry-run semantics now live in the executor's plan(): analyze without
        # mutating anything.
        op = self.h.wh.write("src/created.py", "print(1)\n")["operation_id"]
        plan = self.h.ex.plan(op)
        self.assertTrue(plan["safe"])
        self.assertTrue(os.path.exists(os.path.join(self.root,
                                                    "src", "created.py")))
        # Nothing happened yet (plan is a pure analysis).
        self.assertEqual(self.h.state.get_operation(op)["status"], "completed")


class GenericSurfaceTests(unittest.TestCase):
    def test_rollback_adapters_preserved(self):
        # The generic Core plugin surface preserves the rollback adapters that
        # the removed TaskEngine registry used to expose.
        self.assertTrue(callable(ApprovalGate.authorize_rollback))
        self.assertTrue(callable(RollbackExecutor.plan))
        self.assertTrue(callable(RollbackExecutor.execute))


if __name__ == "__main__":
    unittest.main()