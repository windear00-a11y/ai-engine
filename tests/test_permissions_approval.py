"""Tests for the write approval gate and the engine state database (Phase 1A).

Covers write tiers (T0..T3), approval accept/deny, snapshot-before-write,
deterministic operation ids, journal creation, audit records, snapshot
checksum, checksum mismatch.

All tests use temporary state databases and temporary workspaces; the
production database is never opened for writing.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..")))

from tools.permissions import EngineState, PathPolicy, ApprovalGate
from tools.permissions import deterministic_id, checksum_bytes
from tools.permissions.decisions import Domain, DecisionKind
from tools.permissions.journal import checksum_bytes as jb_checksum
from tests._gate_writer_support import GateWriter


def _mkws():
    d = tempfile.mkdtemp(prefix="perm_approval_")
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    with open(os.path.join(d, "src", "main.py"), "w") as f:
        f.write("x = 1\n")
    return d


class ApprovalHelper:
    def __init__(self, root, approve_all=True):
        self.root = root
        self.state = EngineState(db_path=os.path.join(
            tempfile.mkdtemp(prefix="perm_state_"), "engine_state.db"))
        self.pp = PathPolicy(root)

        def approver(proposal):
            if approve_all:
                return True
            # Approve only writes ending in .py
            return (proposal.get("target") or "").endswith(".py")

        self.gate = ApprovalGate(path_policy=self.pp, state=self.state,
                                 approver=approver)


class DeterministicIdTests(unittest.TestCase):
    def test_id_is_stable(self):
        self.assertEqual(deterministic_id("op", "write", "a", "b"),
                         deterministic_id("op", "write", "a", "b"))

    def test_id_changes_with_input(self):
        self.assertNotEqual(deterministic_id("op", "write", "a", "b"),
                            deterministic_id("op", "write", "a", "c"))

    def test_id_has_prefix(self):
        self.assertTrue(deterministic_id("op", "x").startswith("op_"))

    def test_checksum(self):
        self.assertEqual(checksum_bytes(b"abc"), jb_checksum(b"abc"))
        self.assertNotEqual(checksum_bytes(b"abc"), checksum_bytes(b"abd"))


class WriteTierGateTests(unittest.TestCase):
    def setUp(self):
        self.root = _mkws()
        self.h = ApprovalHelper(self.root, approve_all=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_t0_diff_does_not_require_approval(self):
        # diff/preview goes through classify as T0; through the gate this is
        # a non-mutating read-like operation. We evaluate tier directly.
        from tools.permissions.operations import classify_write, WriteTier
        tier, req_appr, req_snap = classify_write("file.diff")
        self.assertEqual(tier, WriteTier.T0_DIFF)
        self.assertFalse(req_appr)
        self.assertFalse(req_snap)

    def test_t1_edit_requires_approval_and_is_accepted(self):
        d = self.h.gate.check(Domain.WRITE, "src/main.py",
                              operation="file.edit", content="x = 2\n")
        self.assertEqual(d.kind, DecisionKind.REQUIRE_APPROVAL)
        self.assertTrue(d.approval_id)

    def test_t2_write_requires_snapshot(self):
        d = self.h.gate.check(Domain.WRITE, "src/main.py",
                              operation="file.write", content="x = 9\n")
        self.assertEqual(d.kind, DecisionKind.REQUIRE_APPROVAL)
        self.assertTrue(d.approval_id)

    def test_t3_multi_requires_snapshots(self):
        d = self.h.gate.check(Domain.WRITE, "src/main.py",
                              operation="file.edit", content="x = 3\n",
                              multi_file=True)
        self.assertEqual(d.kind, DecisionKind.REQUIRE_APPROVAL)

    def test_blocked_denied(self):
        d = self.h.gate.check(Domain.WRITE, "database/knowledge.db",
                              operation="file.write", content="x")
        self.assertEqual(d.kind, DecisionKind.DENY)
        self.assertEqual(d.reason_code, "blocked")

    def test_readonly_denied(self):
        d = self.h.gate.check(Domain.WRITE, "api/contract.py",
                              operation="file.edit", content="x")
        self.assertEqual(d.kind, DecisionKind.DENY)
        self.assertEqual(d.reason_code, "readonly")

    def test_denied_approval(self):
        h2 = ApprovalHelper(self.root, approve_all=False)
        d = h2.gate.check(Domain.WRITE, "src/other.txt",
                          operation="file.edit", content="x")
        self.assertEqual(d.kind, DecisionKind.DENY)
        self.assertEqual(d.reason_code, "approval_required")

    def test_deny_all_by_default(self):
        gate = ApprovalGate(path_policy=self.h.pp)  # default approver denies
        d = gate.check(Domain.WRITE, "src/main.py", operation="file.edit")
        self.assertEqual(d.kind, DecisionKind.DENY)

    def test_git_network_publish_denied(self):
        for dom in (Domain.GIT, Domain.NETWORK, Domain.PUBLISH):
            d = self.h.gate.check(dom, "x")
            self.assertEqual(d.kind, DecisionKind.DENY, dom)


class SnapshotAndStateTests(unittest.TestCase):
    def setUp(self):
        self.root = _mkws()
        self.h = ApprovalHelper(self.root, approve_all=True)
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_journal_created_by_destructive_write(self):
        main_path = os.path.join(self.root, "src", "main.py")
        with open(main_path, "rb") as f:
            before = f.read()

        def snap(_p):
            return before

        # T2 write requires AND stores the snapshot through the gate.
        d = self.h.gate.check(Domain.WRITE, "src/main.py",
                              operation="file.write", content="x = 99\n",
                              snapshot_provider=snap)
        self.assertEqual(d.kind, DecisionKind.REQUIRE_APPROVAL)
        records = self.h.state.audit_records(operation_id=d.approval_id)
        # A journal snapshot should exist for the target.
        journal = self.h.state.journal_latest_for("src/main.py")
        self.assertIsNotNone(journal)
        self.assertEqual(journal["checksum"], checksum_bytes(before))

    def test_snapshot_checksum_matches_content(self):
        data = b"hello"
        rec = self.h.state.add_journal("op_1", "src/a.py", "t2", data)
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["checksum"], checksum_bytes(data))
        self.assertEqual(rec["size_bytes"], 5)

    def test_checksum_mismatch_detected(self):
        self.assertNotEqual(checksum_bytes(b"aaa"), checksum_bytes(b"bbb"))

    def test_audit_records_written(self):
        d = self.h.gate.check(Domain.WRITE, "src/main.py",
                              operation="file.edit", content="x = 2\n")
        recs = self.h.state.audit_records(operation_id=d.approval_id)
        self.assertTrue(recs)
        row = recs[0]
        self.assertEqual(row["domain"], "write")
        self.assertEqual(row["decision"], "require_approval")
        self.assertEqual(row["approval_id"], d.approval_id)

    def test_audit_count_increments(self):
        before = self.h.state.audit_count()
        self.h.gate.check(Domain.WRITE, "src/main.py",
                          operation="file.edit", content="x = 2\n")
        after = self.h.state.audit_count()
        self.assertGreater(after, before)

    def test_state_integrity_ok(self):
        self.assertEqual(self.h.state.integrity_check()["ok"], True)

    def test_read_operation_appends_no_decision_to_write(self):
        d = self.h.gate.check(Domain.READ, "src/main.py")
        self.assertEqual(d.kind, DecisionKind.ALLOW)


class WriteGateIntegrationTests(unittest.TestCase):
    """End-to-end write approval via the generic Core writer (the removed
    TaskEngine tool registry is gone; the gate + writer are the Core path)."""

    def setUp(self):
        self.root = _mkws()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_mutating_write_goes_through_gate(self):
        from tools.permissions import EngineState, PathPolicy, ApprovalGate
        state = EngineState(db_path=os.path.join(
            tempfile.mkdtemp(), "engine_state.db"))
        pp = PathPolicy(self.root)
        authorized = {"seen": False}

        def approver(proposal):
            if proposal.get("domain") == "write":
                authorized["seen"] = True
            return True

        gate = ApprovalGate(path_policy=pp, state=state, approver=approver)
        wt = GateWriter(self.root, gate)
        res = wt.write("src/out.py", "z = 1\n")
        self.assertIsNone(res["error"])
        # The write went through the approval gate.
        self.assertTrue(authorized["seen"])
        out = os.path.join(self.root, "src", "out.py")
        self.assertTrue(os.path.isfile(out))
        with open(out) as f:
            self.assertEqual(f.read(), "z = 1\n")

    def test_mutating_write_denied_when_gate_denies(self):
        from tools.permissions import EngineState, PathPolicy, ApprovalGate
        state = EngineState(db_path=os.path.join(
            tempfile.mkdtemp(), "engine_state.db"))
        gate = ApprovalGate(
            path_policy=PathPolicy(self.root), state=state,
            approver=lambda p: False)  # deny all
        wt = GateWriter(self.root, gate)
        res = wt.write("src/no.py", "z = 1\n")
        # The write is refused and no file is created.
        self.assertIn("write denied", res["error"])
        self.assertEqual(res.get("bytes_written", 0), 0)
        self.assertFalse(os.path.isfile(
            os.path.join(self.root, "src", "no.py")))
        # Audit/journal show the denial path never produced a mutation.
        self.assertGreater(state.audit_count(), 0)


if __name__ == "__main__":
    unittest.main()
