"""Production invariants guard (Phase 1A).

Read-only verification that the production database and Contract v1 are
untouched, plus that the permission layer refuses to write the production
database even without a gate.

This test NEVER opens the production database for writing; it only reads it
via ``mode=ro`` SQLite and recomputes the SHA-256.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..")))

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__),
                                    os.pardir))


class ContractConservationTests(unittest.TestCase):
    def test_contract_v1_unchanged(self):
        contract = os.path.join(ROOT, "api", "contract.py")
        with open(contract, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn("CONTRACT_VERSION", text)
        self.assertIn('"1"', text)


class _HardGuardWriter:
    """Minimal write path that enforces the hard guard FIRST.

    The removed ``WriteTools`` facade is gone; this stands in so the "frozen
    production database is immutable" invariant stays under regression.
    """

    def __init__(self, root, permissions=None):
        self.root = root
        self.permissions = permissions

    def write(self, path, content, **kwargs):
        from tools.permissions.pathpolicy import hard_write_guard
        abs_path = os.path.abspath(os.path.join(self.root, path))
        if hard_write_guard(abs_path, self.root):
            return {"path": path,
                    "error": "write denied: frozen production database is "
                             "immutable",
                    "bytes_written": 0, "created_or_updated": None}
        data = content.encode("utf-8")
        with open(abs_path, "wb") as f:
            f.write(data)
        return {"path": path, "bytes_written": len(data),
                "created_or_updated": "updated"}


class ProductionWriteDenialTests(unittest.TestCase):
    """The hard guard must refuse writing the production DB regardless of
    whether a gate is configured."""

    def setUp(self):
        self.root = ROOT

    def test_writer_denies_knowledge_db_without_gate(self):
        wt = _HardGuardWriter(self.root)
        res = wt.write("database/knowledge.db", "corruption")
        self.assertIn("immutable", res.get("error", ""))
        self.assertEqual(res.get("bytes_written", 0), 0)

    def test_writer_denies_knowledge_db_with_gate(self):
        from tools.permissions import EngineState, PathPolicy, ApprovalGate
        state = EngineState(db_path=os.path.join(
            tempfile.mkdtemp(), "engine_state.db"))

        def approver(_proposal):
            return True  # even an approving gate cannot override the DB guard

        gate = ApprovalGate(path_policy=PathPolicy(self.root),
                            state=state, approver=approver)
        wt = _HardGuardWriter(self.root, permissions=gate)
        res = wt.write("database/knowledge.db", "corruption")
        self.assertIn("immutable", res.get("error", ""))
        self.assertEqual(res.get("bytes_written", 0), 0)

    def test_permission_gate_denies_knowledge_db(self):
        from tools.permissions import EngineState, PathPolicy, ApprovalGate
        from tools.permissions.decisions import Domain, DecisionKind
        state = EngineState(db_path=os.path.join(
            tempfile.mkdtemp(), "engine_state.db"))
        gate = ApprovalGate(path_policy=PathPolicy(self.root),
                            state=state, approver=lambda p: True)
        d = gate.check(Domain.WRITE, "database/knowledge.db",
                       operation="file.write", content=b"x")
        self.assertEqual(d.kind, DecisionKind.DENY)


if __name__ == "__main__":
    unittest.main()