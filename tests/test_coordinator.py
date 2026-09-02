import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.permissions import EngineState, PathPolicy, ApprovalGate, Policy
from engine.task_engine import TaskEngine, DEFAULT_KNOWLEDGE_DIR
from engine.coordinator import PersistentCoordinator, _map_terminal

PROD_KB_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"


class CoordinatorHarness:
    def __init__(self, approver=None):
        self.root = tempfile.mkdtemp(prefix="coord_")
        self.state = EngineState(db_path=os.path.join(
            tempfile.mkdtemp(prefix="coord_state_"), "engine_state.db"))
        self.gate = ApprovalGate(path_policy=PathPolicy(self.root,
                                                        policy=Policy()),
                                 state=self.state,
                                 approver=approver or (lambda proposal: False))
        self.engine = TaskEngine(knowledge_dir=DEFAULT_KNOWLEDGE_DIR,
                                 workspace_root=self.root,
                                 permissions=self.gate)
        self.coord = PersistentCoordinator(self.state, self.engine,
                                           owner_token="owner-1")
        self.add_cleanup = None

    def cleanup(self):
        shutil.rmtree(self.root, ignore_errors=True)


def write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class CoordinatorTests(unittest.TestCase):
    def make(self, approver=None):
        h = CoordinatorHarness(approver=approver)
        self.addCleanup(h.cleanup)
        return h

    def run_task(self, coordinator, task):
        sub = coordinator.submit(task)
        self.assertTrue(sub["ok"], sub)
        res = coordinator.run(task["id"])
        return res

    # -- invariants ------------------------------------------------------

    def test_production_kb_hash_unchanged(self):
        import hashlib
        path = os.path.join(os.path.dirname(__file__), "..",
                            "database", "knowledge.db")
        with open(path, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(),
                             PROD_KB_SHA)

    # -- submit ----------------------------------------------------------

    def test_submit_creates_planned_task(self):
        h = self.make()
        task = {"id": "t1", "steps": []}
        sub = h.coord.submit(task)
        self.assertTrue(sub["ok"])
        self.assertEqual(sub["status"], "planned")
        row = h.state.get_task("t1")
        self.assertEqual(row["status"], "planned")
        self.assertEqual(json.loads(row["task_json"])["id"], "t1")

    def test_submit_rejects_invalid_plan(self):
        h = self.make()
        task = {"id": "bad", "steps": [{"id": "s1", "tool": "nope"}]}
        sub = h.coord.submit(task)
        self.assertFalse(sub["ok"])
        self.assertIsNone(h.state.get_task("bad"))

    def test_submit_idempotent_identical(self):
        h = self.make()
        task = {"id": "t2", "steps": []}
        self.assertTrue(h.coord.submit(task)["ok"])
        res = h.coord.submit(task)
        self.assertTrue(res["ok"])
        self.assertFalse(res["created"])

    # -- claim / ownership -----------------------------------------------

    def test_second_claim_fails_closed(self):
        h = self.make()
        task = {"id": "t3", "steps": []}
        h.coord.submit(task)
        first = h.state.claim_task("t3", "owner-1")
        self.assertTrue(first["claimed"])
        second = h.state.claim_task("t3", "owner-2")
        self.assertFalse(second["claimed"])

    def test_run_by_wrong_owner_fails_closed(self):
        h = self.make()
        task = {"id": "t4", "steps": []}
        h.coord.submit(task)
        h.state.claim_task("t4", "other-owner")
        res = h.coord.run("t4")
        self.assertFalse(res["ok"])
        self.assertFalse(res["claimed"])

    # -- lifecycle -------------------------------------------------------

    def test_full_lifecycle_read_steps(self):
        h = self.make()
        task = {
            "id": "t5",
            "description": "search then read",
            "steps": [
                {"id": "s1", "tool": "knowledge.search",
                 "inputs": {"query": "react"}},
                {"id": "s2", "tool": "knowledge.get",
                 "inputs": {"node_id": {"$ref": "s1.results[0].id"}}},
            ],
        }
        res = self.run_task(h.coord, task)
        self.assertEqual(res["status"], "completed")
        self.assertTrue(res["ok"])
        self.assertTrue(res["claimed"])
        self.assertEqual(res["steps"][0]["status"], "success")
        self.assertEqual(res["steps"][1]["status"], "success")
        row = h.state.get_task("t5")
        self.assertEqual(row["status"], "completed")
        self.assertIsNotNone(row["finished_at_epoch"])
        steps = h.state.list_task_steps("t5")
        self.assertEqual([s["status"] for s in steps],
                         ["success", "success"])

    def test_byte_identical_with_inmemory_engine(self):
        h = self.make()
        task = {
            "id": "t6",
            "steps": [
                {"id": "s1", "tool": "knowledge.search",
                 "inputs": {"query": "sqlite"}},
            ],
        }
        # Persisted path.
        sub = h.coord.submit(task)
        self.assertTrue(sub["ok"])
        res = h.coord.run("t6")
        # Pure in-memory path on the same task.
        mem = TaskEngine(knowledge_dir=DEFAULT_KNOWLEDGE_DIR,
                         workspace_root=h.root).run_task(task)
        self.assertEqual(res["status"], mem["status"])
        self.assertEqual(res["step_count"], mem["step_count"])
        for p, m in zip(res["steps"], mem["steps"]):
            self.assertEqual(p["status"], m["status"])
            self.assertEqual(p["tool"], m["tool"])
            self.assertEqual(p["result"], m["result"])
        # Every persisted transcript record must carry the task identity.
        for p in res["steps"]:
            self.assertEqual(p["task_id"], "t6")

    def test_failed_step_marks_task_failed(self):
        h = self.make()
        task = {
            "id": "t7",
            "steps": [
                {"id": "s1", "tool": "knowledge.get",
                 "inputs": {"node_id": "does-not-exist"}},
            ],
            "stop_on_error": True,
        }
        res = self.run_task(h.coord, task)
        self.assertEqual(res["status"], "failed")
        self.assertFalse(res["ok"])
        row = h.state.get_task("t7")
        self.assertEqual(row["status"], "failed")
        self.assertIsNotNone(row["finished_at_epoch"])
        steps = h.state.list_task_steps("t7")
        self.assertEqual(steps[0]["status"], "failed")

    def test_stop_on_error_false_continues(self):
        h = self.make()
        task = {
            "id": "t8",
            "steps": [
                {"id": "s1", "tool": "knowledge.get",
                 "inputs": {"node_id": "nope-nonexistent"}},
                {"id": "s2", "tool": "knowledge.search",
                 "inputs": {"query": "react"}},
            ],
            "stop_on_error": False,
        }
        res = self.run_task(h.coord, task)
        self.assertEqual(res["status"], "completed_with_errors")
        self.assertEqual(res["steps"][1]["status"], "success")
        row = h.state.get_task("t8")
        self.assertEqual(row["status"], "completed")

    # -- dry-run ---------------------------------------------------------

    def test_dry_run_does_not_write(self):
        h = self.make()
        write(h.root, "a.txt", "old\n")
        task = {
            "id": "t9",
            "dry_run": True,
            "steps": [
                {"id": "s1", "tool": "file.write",
                 "inputs": {"path": "a.txt", "content": "new\n"}},
                {"id": "s2", "tool": "file.read", "inputs": {"path": "a.txt"}},
            ],
        }
        res = self.run_task(h.coord, task)
        self.assertEqual(res["status"], "planned")
        self.assertTrue(res["planned"])
        self.assertEqual(len(res["planned_actions"]), 1)
        with open(os.path.join(h.root, "a.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "old\n")
        steps = h.state.list_task_steps("t9")
        stat = {s["step_id"]: s["status"] for s in steps}
        self.assertEqual(stat["s1"], "planned")
        self.assertEqual(stat["s2"], "success")
        # No journal operation was created for the planned write.
        ops = [s["operation_id"] for s in h.state.list_task_steps("t9")]
        self.assertEqual(ops, [None, None])

    # -- write approval + attach ------------------------------------------

    def test_approved_write_attaches_operation(self):
        h = self.make(approver=lambda proposal: True)
        task = {
            "id": "t10",
            "steps": [
                {"id": "s1", "tool": "file.write",
                 "inputs": {"path": "out.txt", "content": "hello\n"}},
            ],
        }
        res = self.run_task(h.coord, task)
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["steps"][0]["status"], "success")
        op_id = res["steps"][0]["result"]["operation_id"]
        self.assertTrue(op_id.startswith("op_"))
        step = h.state.get_task_step("t10", "s1")
        self.assertEqual(step["operation_id"], op_id)
        self.assertEqual(h.state.get_operation(op_id)["status"], "completed")

    def test_denied_write_fails_step_closed(self):
        h = self.make(approver=lambda proposal: False)
        task = {
            "id": "t11",
            "steps": [
                {"id": "s1", "tool": "file.write",
                 "inputs": {"path": "out.txt", "content": "hello\n"}},
            ],
        }
        res = self.run_task(h.coord, task)
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["steps"][0]["status"], "failed")
        step = h.state.get_task_step("t11", "s1")
        self.assertEqual(step["status"], "failed")
        self.assertIsNone(step["operation_id"])

    # -- corrupt / invalid inputs ------------------------------------------

    def test_corrupt_stored_task_fails_closed(self):
        h = self.make()
        task = {"id": "t12", "steps": []}
        h.coord.submit(task)
        import sqlite3
        conn = sqlite3.connect(h.state.db_path)
        conn.execute("UPDATE tasks SET task_json = 'not json' "
                     "WHERE task_id = 't12'")
        conn.commit()
        conn.close()
        res = h.coord.run("t12")
        self.assertFalse(res["ok"])
        row = h.state.get_task("t12")
        self.assertEqual(row["status"], "invalid")

    # -- mapping ------------------------------------------------------------

    def test_terminal_mapping(self):
        self.assertEqual(_map_terminal("completed"), "completed")
        self.assertEqual(_map_terminal("failed"), "failed")
        self.assertEqual(_map_terminal("planned"), "completed")
        self.assertEqual(_map_terminal("completed_with_errors"), "completed")
        self.assertEqual(_map_terminal("invalid"), "invalid")


if __name__ == "__main__":
    unittest.main()