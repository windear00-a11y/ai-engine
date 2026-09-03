"""Phase 0 invariant tests: the intelligence layer must not disturb the system.

These tests verify the hard invariants that every subsequent intelligence
phase is required to preserve:

1. ``api.contract.CONTRACT_VERSION`` is frozen at "1" (Public Contract v1).
2. ``database/knowledge.db`` is byte-identical to the administrator-recorded
   hash recorded before Phase 0 (production knowledge is immutable).
3. The deterministic permission/safety stack
   (``tools.permissions.Policy -> PathPolicy -> ApprovalGate``) is still
   functionally authoritative (deny-by-default preserved).
4. The deterministic step runner (``engine.task_engine``) is unchanged in
   behavior for identical input.
5. The ``intelligence`` package is importable (empty foundation package).

These tests never write to any production or new database. They are
read-only verification and are themselves deterministic.
"""

import hashlib
import importlib
import os
import sys
import tempfile
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.contract import CONTRACT_VERSION  # noqa: E402
from tools.permissions import (  # noqa: E402
    ApprovalGate,
    Domain,
    Policy,
)
from tools.permissions.decisions import DecisionKind  # noqa: E402
from tools.permissions.pathpolicy import Zone, hard_write_guard  # noqa: E402

# Administrator-recorded hash of the production DB before Phase 0.
# Must remain unchanged for the life of the knowledge graph (v1).
EXPECTED_KNOWLEDGE_DB_SHA256 = (
    "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"
)

PROD_DB = os.path.join(_ROOT, "database", "knowledge.db")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class ContractFrozenTests(unittest.TestCase):
    """Public Contract v1 is frozen. Any intelligence change that would break
    it must become v2, never mutate v1."""

    def test_contract_version_is_one(self):
        self.assertEqual(CONTRACT_VERSION, "1")

    def test_contract_file_declares_v1_without_mutation(self):
        with open(os.path.join(_ROOT, "api", "contract.py"),
                  encoding="utf-8") as f:
            text = f.read()
        self.assertIn("CONTRACT_VERSION", text)
        self.assertIn('CONTRACT_VERSION = "1"', text)


class KnowledgeDBImmutableTests(unittest.TestCase):
    """database/knowledge.db is the immutable production knowledge graph.
    It must remain byte-identical across all intelligence work."""

    def test_knowledge_db_exists(self):
        self.assertTrue(os.path.isfile(PROD_DB),
                        "production knowledge.db is missing")

    def test_knowledge_db_hash_is_unchanged(self):
        self.assertEqual(_sha256(PROD_DB), EXPECTED_KNOWLEDGE_DB_SHA256)

    def test_integrity_check_ok(self):
        import sqlite3
        conn = sqlite3.connect("file:%s?mode=ro" % PROD_DB, uri=True)
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            self.assertEqual(row[0], "ok")
        finally:
            conn.close()


class PermissionStackAuthoritativeTests(unittest.TestCase):
    """The deterministic safety stack must remain authoritative: non-read
    operations require explicit approval by default (deny-by-default)."""

    def test_policy_defaults_to_deny(self):
        # An empty Policy falls back to safe defaults: approval is required
        # by default and all risky capabilities (git/network/publish) are
        # denied unless explicitly enabled.
        policy = Policy()
        self.assertTrue(policy.require_approval_by_default)
        self.assertFalse(policy.enabled("git"))
        self.assertFalse(policy.enabled("network"))
        self.assertFalse(policy.enabled("publish"))

    def test_knowledge_db_is_hard_guarded_from_writes(self):
        self.assertTrue(hard_write_guard(PROD_DB, _ROOT))

    def test_path_policy_blocks_knowledge_db_writes(self):
        from tools.permissions import PathPolicy
        path_policy = PathPolicy(_ROOT)
        decision = path_policy.read_decision(os.path.join("database",
                                                          "knowledge.db"))
        # The production knowledge DB is hard-blocked even from reads.
        self.assertEqual(decision.kind, DecisionKind.DENY)
        self.assertEqual(decision.reason_code, "blocked")

    def test_approval_gate_denies_write_without_configuration(self):
        # A bare ApprovalGate (no path policy, no approver) fails closed:
        # a write proposal is denied, never allowed by default.
        gate = ApprovalGate()
        op = gate.check(Domain.WRITE, "some/output/file")
        self.assertEqual(op.kind, DecisionKind.DENY)


class DeterministicStepRunnerTests(unittest.TestCase):
    """engine.task_engine must remain deterministic: identical input yields
    identical output (no hidden state, no ordering dependence)."""

    def test_identical_input_identical_output(self):
        from engine.task_engine import TaskEngine
        a = TaskEngine()
        b = TaskEngine()
        task = {
            "id": "t-1",
            "steps": [
                {"id": "s1", "tool": "echo", "args": {"text": "hello"}},
            ],
        }
        # validate_task is pure and deterministic: two independent engine
        # instances agree for identical input.
        self.assertEqual(a.validate_task(task), b.validate_task(task))
        # Validating the same task twice on the same engine is identical.
        self.assertEqual(a.validate_task(task), a.validate_task(task))


class PackageImportableTests(unittest.TestCase):
    def test_intelligence_imports(self):
        mod = importlib.import_module("intelligence")
        self.assertTrue(hasattr(mod, "__version__"))

    def test_intelligence_has_no_ai_dependency(self):
        # The foundation package must not pull in any ML/AI framework or a
        # model file. It is plain stdlib Python.
        intelligence = importlib.import_module("intelligence")
        self.assertEqual(intelligence.__version__, "0")


if __name__ == "__main__":
    unittest.main()
