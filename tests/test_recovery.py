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
from engine.recovery import reconcile, REASON_INTERRUPTED_RESTART

PROD_KB_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"


class RecoveryHarness:
    def __init__(self):
        self.root = tempfile.mkdtemp(prefix="recovery_")
        self.state = EngineState(db_path=os.path.join(
            tempfile.mkdtemp(prefix="recovery_state_"), "engine_state.db"))
        self.gate = ApprovalGate(path_policy=PathPolicy(self.root,
                                                        policy=Policy()),
                                 state=self.state,
                                 approver=lambda proposal: True)
        self.engine = TaskEngine(knowledge_dir=DEFAULT_KNOWLEDGE_DIR,
                                 workspace_root=self.root,
                                 permissions=self.gate)
        self.coord = PersistentCoordinator(self.state, self.engine,
                                           owner_token="owner-1")

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def simulate_crashed_run(state, workspace_root, task_id, success_steps,
                         running_step, pending_steps):
    """Rebuild the exact post-crash write-ahead shape for a claimed task."""
    task = {
        "id": task_id,
        "steps": ([{"id": s, "tool": "knowledge.search", "inputs": {"query": "x"}}
                   for s in success_steps] +
                  [{"id": running_step, "tool": "knowledge.search",
                    "inputs": {"query": "x"}}] +
                  [{"id": s, "tool": "knowledge.search", "inputs": {"query": "x"}}
                   for s in pending_steps]),
    }
    import json as _json
    state.create_task(task_id, task, workspace_root, status="planned")
    ok = state.claim_task(task_id, "owner-1")
    assert ok["claimed"], ok
    for i, sid in enumerate(success_steps):
        state.ensure_task_step(task_id, sid, i, "knowledge.search",
                               {"query": "x"}, status="success")
    if running_step:
        r_i = len(success_steps)
        state.ensure_task_step(task_id, running_step, r_i, "knowledge.search",
                               {"query": "x"}, status="running")
    for j, sid in enumerate(pending_steps):
        state.ensure_task_step(task_id, sid, j + len(success_steps) + 1,
                               "knowledge.search", {"query": "x"},
                               status="pending")
    return task


class RecoveryTests(unittest.TestCase):
    def make(self):
        h = RecoveryHarness()
        self.addCleanup(h.cleanup)
        return h

    def test_empty_state_noop(self):
        h = self.make()
        res = reconcile(h.state)
        self.assertEqual(res, {"reconciled": [], "count": 0})

    def test_reconciles_crashed_task(self):
        h = self.make()
        task = simulate_crashed_run(h.state, h.root, "t1", ["s1"], "s2", ["s3"])
        res = reconcile(h.state)
        self.assertEqual(res, {"reconciled": ["t1"], "count": 1})
        row = h.state.get_task("t1")
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["status_reason"], REASON_INTERRUPTED_RESTART)
        steps = {s["step_id"]: s for s in h.state.list_task_steps("t1")}
        self.assertEqual(steps["s1"]["status"], "success")
        self.assertEqual(steps["s2"]["status"], "skipped")
        self.assertEqual(steps["s3"]["status"], "skipped")
        self.assertEqual(steps["s2"]["error"], REASON_INTERRUPTED_RESTART)
        self.assertEqual(steps["s3"]["error"], REASON_INTERRUPTED_RESTART)

    def test_idempotent(self):
        h = self.make()
        simulate_crashed_run(h.state, h.root, "t2", ["s1"], "s2", ["s3"])
        first = reconcile(h.state)
        second = reconcile(h.state)
        self.assertEqual(first["count"], 1)
        self.assertEqual(second, {"reconciled": [], "count": 0})
        row = h.state.get_task("t2")
        self.assertEqual(row["status"], "failed")

    def test_leaves_planned_and_completed_untouched(self):
        h = self.make()
        # Planned (never claimed) task.
        h.state.create_task("t-planned", {"id": "t-planned", "steps": []},
                            h.root, status="planned")
        # Completed task via the real coordinator.
        h.coord.submit({"id": "t-done", "steps": [
            {"id": "x", "tool": "knowledge.search",
             "inputs": {"query": "react"}}]})
        h.coord.run("t-done")
        res = reconcile(h.state)
        self.assertEqual(res, {"reconciled": [], "count": 0})
        self.assertEqual(h.state.get_task("t-planned")["status"], "planned")
        self.assertEqual(h.state.get_task("t-done")["status"], "completed")

    def test_production_kb_hash_unchanged(self):
        path = os.path.join(os.path.dirname(__file__), "..",
                            "database", "knowledge.db")
        with open(path, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(),
                             PROD_KB_SHA)

    def test_recovery_only_reads_via_enginestate(self):
        # engine.recovery must NEVER write via raw sqlite: no schema DDL, no
        # commit, and the only connection it reaches is EngineState's own
        # (bounded, read-only scan, pending a 6F list API).
        import inspect
        import engine.recovery as rec
        src = inspect.getsource(rec)
        self.assertNotIn("executescript", src)
        self.assertNotIn(".commit()", src)
        self.assertNotIn("INSERT INTO", src)
        self.assertNotIn("UPDATE tasks", src)
        self.assertIn("state._connect()", src)
        self.assertIn("SELECT task_id FROM tasks WHERE status = 'running'", src)
        self.assertIn("list_task_steps", src)
        self.assertIn("update_task_status", src)


if __name__ == "__main__":
    unittest.main()