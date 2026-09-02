"""Layer 6E tests: ownership hardening of the persisted task layer.

Covers the deny-by-default cross-owner matrix on every OwnerScope surface:
reads leak nothing, writes produce zero side effects, same-owner calls stay
at parity with raw EngineState, the coordinator lifecycle still works routed
through its scoped access, and the production knowledge.db is untouched.

OwnerScope surface under test:
    reads:  get_task, list_task_steps, get_task_step
    writes: ensure_task_step, update_task_step_status, update_step_result,
            attach_operation, update_task_result, update_task_status
"""
import hashlib
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.permissions import EngineState, PathPolicy, ApprovalGate, Policy
from engine.task_engine import TaskEngine, DEFAULT_KNOWLEDGE_DIR
from engine.coordinator import PersistentCoordinator
from engine.ownership import OwnerScope

PROD_KB_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"


class OwnershipHarness:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="ownership_")
        self.state = EngineState(db_path=os.path.join(
            tempfile.mkdtemp(prefix="ownership_state_"), "engine_state.db"))
        self.gate = ApprovalGate(path_policy=PathPolicy(self.root,
                                                        policy=Policy()),
                                 state=self.state,
                                 approver=lambda proposal: True)
        self.engine = TaskEngine(knowledge_dir=DEFAULT_KNOWLEDGE_DIR,
                                 workspace_root=self.root,
                                 permissions=self.gate)
        self.coord = PersistentCoordinator(self.state, self.engine,
                                           owner_token="owner-A")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def claimed_task(self, task_id="t1"):
        sub = self.coord.submit({"id": task_id, "steps": []})
        assert sub["ok"], sub
        ok = self.state.claim_task(task_id, "owner-A")
        assert ok["claimed"], ok
        return task_id


class OwnerScopeTests(unittest.TestCase):
    def make(self):
        h = OwnershipHarness()
        self.addCleanup(h.cleanup)
        return h

    def snapshot(self, h, tid):
        row = h.state.get_task(tid)
        steps = h.state.list_task_steps(tid)
        return (dict(row) if row else None, [dict(s) for s in steps])

    # -- reads deny without leaking ---------------------------------------

    def test_cross_owner_reads_denied_no_leak(self):
        h = self.make()
        tid = h.claimed_task()
        other = OwnerScope(h.state, "owner-B")
        self.assertIsNone(other.get_task(tid))      # None, not the row
        self.assertEqual(other.list_task_steps(tid), [])
        self.assertIsNone(other.get_task_step(tid, "s1"))
        # owner-A still sees it: existence never disclosed non-owner.
        self.assertIsNotNone(OwnerScope(h.state, "owner-A").get_task(tid))

    def test_missing_task_denied(self):
        h = self.make()
        s = OwnerScope(h.state, "owner-A")
        self.assertIsNone(s.get_task("ghost"))
        res = s.update_task_status("ghost", "rolled_back")
        self.assertFalse(res["ok"])
        self.assertTrue(res["denied"])

    # -- writes deny with zero side effects -------------------------------

    def test_cross_owner_writes_denied_no_side_effect(self):
        h = self.make()
        tid = h.claimed_task()
        own = OwnerScope(h.state, "owner-A")
        own.ensure_task_step(tid, "s1", 0, "knowledge.search",
                             {"query": "x"}, status="success")
        other = OwnerScope(h.state, "owner-B")
        before = self.snapshot(h, tid)

        res = other.update_task_step_status(tid, "s1", "running")
        self.assertFalse(res["ok"]); self.assertTrue(res["denied"])
        res = other.update_step_result(tid, "s1", result_json={"x": 1})
        self.assertFalse(res["ok"]); self.assertTrue(res["denied"])
        res = other.attach_operation(tid, "s1", "op_zzz")
        self.assertFalse(res["ok"]); self.assertTrue(res["denied"])
        res = other.update_task_result(tid, {"task_id": tid})
        self.assertFalse(res["ok"]); self.assertTrue(res["denied"])
        res = other.update_task_status(tid, "rolled_back")
        self.assertFalse(res["ok"]); self.assertTrue(res["denied"])
        res = other.ensure_task_step(tid, "s2", 1, "knowledge.search",
                                     {"query": "y"})
        self.assertFalse(res["ok"]); self.assertTrue(res["denied"])

        self.assertEqual(self.snapshot(h, tid), before)
        self.assertEqual(h.state.get_task(tid)["status"], "running")

    # -- same-owner parity with raw EngineState ---------------------------

    def test_same_owner_parity(self):
        h = self.make()
        tid = h.claimed_task()
        own = OwnerScope(h.state, "owner-A")
        own.ensure_task_step(tid, "s1", 0, "knowledge.search",
                             {"query": "x"}, status="pending")
        self.assertEqual(
            own.list_task_steps(tid),
            h.state.list_task_steps(tid))
        self.assertEqual(own.get_task_step(tid, "s1"),
                         h.state.get_task_step(tid, "s1"))
        self.assertEqual(own.get_task(tid), h.state.get_task(tid))
        up = own.update_task_step_status(tid, "s1", "running")
        self.assertTrue(up["ok"])
        self.assertEqual(h.state.get_task_step(tid, "s1")["status"],
                         "running")

    # -- coordinator lifecycle routed through the scope -------------------

    def test_coordinator_lifecycle_still_works(self):
        h = self.make()
        task = {"id": "t2", "steps": [{"id": "s1", "tool": "file.write",
                                       "inputs": {"path": "x.txt",
                                                  "content": "hi\n"}}]}
        sub = h.coord.submit(task)
        self.assertTrue(sub["ok"])
        res = h.coord.run("t2")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["status"], "completed")
        row = h.state.get_task("t2")
        self.assertEqual(row["status"], "completed")
        op_id = h.state.get_task_step("t2", "s1")["operation_id"]
        self.assertTrue(op_id.startswith("op_"))

    def test_coordinator_wrong_owner_claim_fails_closed(self):
        h = self.make()
        tid = h.claimed_task()
        other = PersistentCoordinator(h.state, h.engine, owner_token="owner-B")
        self.assertFalse(other._claim(tid)["ok"])

    # -- invariants -------------------------------------------------------

    def test_production_kb_hash_unchanged(self):
        path = os.path.join(os.path.dirname(__file__), "..",
                            "database", "knowledge.db")
        with open(path, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(),
                             PROD_KB_SHA)

    def test_owner_scope_reaches_database_only_via_enginestate(self):
        import inspect
        import engine.ownership as own
        src = inspect.getsource(own)
        self.assertNotIn("knowledge.db", src)
        self.assertNotIn("sqlite3", src)
        self.assertNotIn("executescript", src)
        self.assertIn("self.state.get_task", src)