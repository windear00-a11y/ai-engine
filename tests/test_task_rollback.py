"""Layer 6D tests: task-level rollback aggregation.

Covers:
* empty-manifest plan/execute (no-op rollback terminalizes a running task
  that never attached write operations).
* aggregation: every attached operation_id collected in rollback order
  (reverse of step order), deterministic across calls.
* full cycle: an in-flight running task with approved writes rolls back both
  files (restore + delete), operations statuses -> rolled_back, task
  running -> rolled_back with aggregate evidence.
* idempotency: a second execute fails closed (no double rollback, no
  mutation, task stays terminal).
* denied writes are never attached, hence never in scope.
* wrong-owner rollback fails closed.
* external modification -> rollback conflict -> nothing rolled back, task
  stays running (fail closed).
* production knowledge.db SHA invariant.
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
from engine.rollback import TaskRollback, REASON_ROLLED_BACK

PROD_KB_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"


class RollbackHarness:
    def __init__(self, approver=None):
        self.root = tempfile.mkdtemp(prefix="rollback_")
        self.state = EngineState(db_path=os.path.join(
            tempfile.mkdtemp(prefix="rollback_state_"), "engine_state.db"))
        self.gate = ApprovalGate(path_policy=PathPolicy(self.root,
                                                        policy=Policy()),
                                 state=self.state,
                                 approver=approver
                                 or (lambda proposal: True))
        self.engine = TaskEngine(knowledge_dir=DEFAULT_KNOWLEDGE_DIR,
                                 workspace_root=self.root,
                                 permissions=self.gate)
        self.coord = PersistentCoordinator(self.state, self.engine,
                                           owner_token="owner-1")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def make_path(self, rel):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path


def run_inflight(coord, task):
    """Execute every step of a claimed task WITHOUT finalizing it.

    Mirrors coordinator._run_one (persist intent, execute, attach) but stops
    short of the terminal task transition so the task remains ``running`` —
    the exact shape a crashed coordinator leaves behind.
    """
    state, engine = coord.state, coord.engine
    sub = coord.submit(task)
    assert sub["ok"], sub
    claimed = state.claim_task(task["id"], coord.owner_token)
    assert claimed["claimed"], claimed
    engine._current_task_id = task["id"]
    store = {}
    for i, step in enumerate(task["steps"]):
        sid, tool, inputs = step["id"], step["tool"], step["inputs"]
        state.ensure_task_step(task["id"], sid, i, tool, inputs,
                               status="pending")
        state.update_task_step_status(task["id"], sid, "running",
                                      expected_status="pending")
        rec = engine._run_step(step, store, False, None)
        store[sid] = rec
        state.update_task_step_status(task["id"], sid, rec["status"],
                                      expected_status="running")
        state.update_step_result(task["id"], sid, result_json=rec["result"],
                                 error=rec.get("error"),
                                 started_at_epoch=rec.get("started_at"),
                                 finished_at_epoch=rec.get("finished_at"),
                                 duration=rec.get("duration"))
        res = rec["result"]
        op_id = res.get("operation_id") if isinstance(res, dict) else None
        if op_id:
            state.attach_operation(task["id"], sid, op_id)
    return task["id"]


class TaskRollbackTests(unittest.TestCase):
    def make(self, approver=None):
        h = RollbackHarness(approver=approver)
        self.addCleanup(h.cleanup)
        return h

    # -- plan aggregation -------------------------------------------------

    def test_empty_plan_and_empty_execute_noop(self):
        h = self.make()
        tid = run_inflight(h.coord, {
            "id": "t1",
            "steps": [{"id": "s1", "tool": "knowledge.search",
                       "inputs": {"query": "react"}}],
        })
        plan = TaskRollback(h.gate.path_policy, h.state, h.gate).plan(tid)
        self.assertEqual(plan["count"], 0)
        self.assertTrue(plan["safe"])
        self.assertEqual(plan["operations"], [])

        res = h.coord.rollback(tid)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["status"], "rolled_back")
        self.assertEqual(res["rollback_count"], 0)
        self.assertEqual(h.state.get_task(tid)["status"], "rolled_back")

    def test_plan_collects_ops_in_reverse_order(self):
        h = self.make()
        p1, p2 = "a.txt", "b.txt"
        tid = run_inflight(h.coord, {
            "id": "t2",
            "steps": [
                {"id": "s1", "tool": "file.write",
                 "inputs": {"path": p1, "content": "one\n"}},
                {"id": "s2", "tool": "file.write",
                 "inputs": {"path": p2, "content": "two\n"}},
            ],
        })
        steps = {s["step_id"]: s for s in h.state.list_task_steps(tid)}
        op1 = steps["s1"]["operation_id"]
        op2 = steps["s2"]["operation_id"]
        self.assertTrue(op1.startswith("op_"))
        self.assertTrue(op2.startswith("op_"))
        self.assertNotEqual(op1, op2)

        rb = TaskRollback(h.gate.path_policy, h.state, h.gate)
        first = rb.plan(tid)
        second = rb.plan(tid)
        self.assertEqual(first["rollback_order"], [op2, op1])
        self.assertEqual(first, second)  # deterministic

    # -- full cycle -------------------------------------------------------

    def test_full_cycle_restores_rolls_back_and_terminalizes(self):
        h = self.make()
        edit_path = h.make_path("src/main.py")
        with open(edit_path, "w") as f:
            f.write("original\n")
        tid = run_inflight(h.coord, {
            "id": "t3",
            "steps": [
                {"id": "s1", "tool": "file.edit",
                 "inputs": {"path": "src/main.py",
                            "old_text": "original\n", "new_text": "edited\n"}},
                {"id": "s2", "tool": "file.write",
                 "inputs": {"path": "new.txt", "content": "extra\n"}},
            ],
        })
        with open(edit_path) as f:
            self.assertEqual(f.read(), "edited\n")
        self.assertTrue(os.path.exists(h.make_path("new.txt")))
        steps = {s["step_id"]: s for s in h.state.list_task_steps(tid)}
        op1, op2 = steps["s1"]["operation_id"], steps["s2"]["operation_id"]

        res = h.coord.rollback(tid)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["status"], "rolled_back")
        self.assertEqual(res["rollback_count"], 2)
        self.assertEqual(res["rolled_back_operations"], [op2, op1])
        with open(edit_path) as f:
            self.assertEqual(f.read(), "original\n")
        self.assertFalse(os.path.exists(h.make_path("new.txt")))
        row = h.state.get_task(tid)
        self.assertEqual(row["status"], "rolled_back")
        self.assertEqual(row["status_reason"], REASON_ROLLED_BACK)
        self.assertEqual(h.state.get_operation(op1)["status"], "rolled_back")
        self.assertEqual(h.state.get_operation(op2)["status"], "rolled_back")

    def test_idempotent_execute_fails_closed(self):
        h = self.make()
        path = h.make_path("keep.txt")
        with open(path, "w") as f:
            f.write("before\n")
        tid = run_inflight(h.coord, {
            "id": "t4",
            "steps": [
                {"id": "s1", "tool": "file.edit",
                 "inputs": {"path": "keep.txt",
                            "old_text": "before\n", "new_text": "after\n"}},
            ],
        })
        first = h.coord.rollback(tid)
        self.assertTrue(first["ok"], first)
        with open(path) as f:
            self.assertEqual(f.read(), "before\n")

        before = h.state.get_task(tid)
        second = h.coord.rollback(tid)
        self.assertFalse(second["ok"])
        with open(path) as f:
            self.assertEqual(f.read(), "before\n")
        after = h.state.get_task(tid)
        self.assertEqual(after["status"], "rolled_back")
        self.assertEqual(after["updated_at_epoch"],
                         before["updated_at_epoch"])

    # -- deny-by-default --------------------------------------------------

    def test_denied_writes_never_in_scope(self):
        h = self.make(approver=lambda proposal: False)
        path = h.make_path("out.txt")
        with open(path, "w") as f:
            f.write("keep\n")
        tid = run_inflight(h.coord, {
            "id": "t5",
            "steps": [
                {"id": "s1", "tool": "file.write",
                 "inputs": {"path": "out.txt", "content": "nope\n"}},
            ],
        })
        step = h.state.get_task_step(tid, "s1")
        self.assertIsNone(step["operation_id"])
        rb = TaskRollback(h.gate.path_policy, h.state, h.gate)
        self.assertEqual(rb.plan(tid)["operations"], [])
        res = h.coord.rollback(tid)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["rollback_count"], 0)
        with open(path) as f:
            self.assertEqual(f.read(), "keep\n")

    def test_wrong_owner_rollback_fails_closed(self):
        h = self.make()
        tid = run_inflight(h.coord, {
            "id": "t6",
            "steps": [{"id": "s1", "tool": "knowledge.search",
                       "inputs": {"query": "x"}}],
        })
        other = PersistentCoordinator(h.state, h.engine, owner_token="evil")
        res = other.rollback(tid)
        self.assertFalse(res["ok"])
        self.assertEqual(h.state.get_task(tid)["status"], "running")

    def test_external_modification_conflict_fails_closed(self):
        h = self.make()
        path = h.make_path("conflict.txt")
        with open(path, "w") as f:
            f.write("mom\n")
        tid = run_inflight(h.coord, {
            "id": "t7",
            "steps": [
                {"id": "s1", "tool": "file.edit",
                 "inputs": {"path": "conflict.txt",
                            "old_text": "mom\n", "new_text": "edited\n"}},
            ],
        })
        # External agent edits the file after our write -> TOCTOU.
        with open(path, "w") as f:
            f.write("externally modified\n")
        res = h.coord.rollback(tid)
        self.assertFalse(res["ok"])
        self.assertIn("rollback conflicts detected", res["error"])
        with open(path) as f:
            self.assertEqual(f.read(), "externally modified\n")
        self.assertEqual(h.state.get_task(tid)["status"], "running")

    # -- invariants -------------------------------------------------------

    def test_production_kb_hash_unchanged(self):
        path = os.path.join(os.path.dirname(__file__), "..",
                            "database", "knowledge.db")
        with open(path, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(),
                             PROD_KB_SHA)

    def test_rollback_writes_only_via_enginestate(self):
        import inspect
        import engine.rollback as rbmod
        import engine.coordinator as cmod
        src = inspect.getsource(rbmod) + inspect.getsource(cmod)
        self.assertNotIn("executescript", src)
        self.assertNotIn(".commit(", src)
        self.assertIn("RollbackExecutor", src)
        self.assertIn("authorize_rollback", src)