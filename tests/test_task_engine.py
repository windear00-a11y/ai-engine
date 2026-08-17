import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.task_engine import TaskEngine, run_task, DEFAULT_KNOWLEDGE_DIR


def write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class TaskEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = TaskEngine(knowledge_dir=DEFAULT_KNOWLEDGE_DIR,
                                 workspace_root=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    # -- valid execution + references ------------------------------------

    def test_valid_task_execution_with_knowledge_ref(self):
        task = {
            "id": "t-knowledge",
            "description": "search then get top result",
            "steps": [
                {"id": "s1", "tool": "knowledge.search",
                 "inputs": {"query": "react"}},
                {"id": "s2", "tool": "knowledge.get",
                 "inputs": {"node_id": {"$ref": "s1.results[0].id"}}},
            ],
            "stop_on_error": True,
        }
        res = self.engine.run_task(task)
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["steps"][0]["status"], "success")
        self.assertEqual(res["steps"][1]["status"], "success")
        expected = res["steps"][0]["result"]["results"][0]["id"]
        self.assertEqual(res["steps"][1]["result"]["id"], expected)

    def test_passing_result_between_coding_steps(self):
        write(self.tmp.name, "hello.txt", "content here")
        task = {
            "id": "t-chain",
            "steps": [
                {"id": "list", "tool": "file.list",
                 "inputs": {"path": ".", "include_dirs": False}},
                {"id": "read", "tool": "file.read",
                 "inputs": {"path": {"$ref": "list.entries[0].path"}}},
            ],
            "stop_on_error": True,
        }
        res = self.engine.run_task(task)
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["steps"][1]["result"]["content"], "content here")

    def test_step_ordering_preserved(self):
        task = {
            "id": "t-order",
            "steps": [
                {"id": "a", "tool": "project.inspect", "inputs": {}},
                {"id": "b", "tool": "project.inspect", "inputs": {}},
            ],
        }
        res = self.engine.run_task(task)
        self.assertEqual([s["id"] for s in res["steps"]], ["a", "b"])

    # -- invalid inputs / tools ------------------------------------------

    def test_invalid_tool_rejected(self):
        task = {
            "id": "t-badtool",
            "steps": [
                {"id": "s1", "tool": "evil.something", "inputs": {}},
            ],
        }
        ok, errors = self.engine.validate_task(task)
        self.assertFalse(ok)
        res = self.engine.run_task(task)
        self.assertEqual(res["status"], "invalid")
        self.assertTrue(any("evil.something" in e for e in res["errors"]))

    def test_invalid_step_input_missing_arg(self):
        task = {
            "id": "t-badinput",
            "steps": [
                {"id": "s1", "tool": "file.read", "inputs": {}},
            ],
            "stop_on_error": True,
        }
        res = self.engine.run_task(task)
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["steps"][0]["status"], "failed")
        self.assertIn("invalid input", res["steps"][0]["error"])

    def test_invalid_step_input_not_object(self):
        task = {
            "id": "t-badinput2",
            "steps": [
                {"id": "s1", "tool": "file.list", "inputs": "not-an-object"},
            ],
        }
        res = self.engine.run_task(task)
        self.assertEqual(res["steps"][0]["status"], "failed")
        self.assertIn("inputs must be an object", res["steps"][0]["error"])

    def test_unresolvable_reference(self):
        task = {
            "id": "t-ref",
            "steps": [
                {"id": "s1", "tool": "project.inspect", "inputs": {}},
                {"id": "s2", "tool": "file.read",
                 "inputs": {"path": {"$ref": "s1.nope[0].x"}}},
            ],
        }
        res = self.engine.run_task(task)
        self.assertEqual(res["steps"][1]["status"], "failed")
        self.assertIn("reference error", res["steps"][1]["error"])

    # -- failure handling -------------------------------------------------

    def test_stop_on_error(self):
        task = {
            "id": "t-stop",
            "steps": [
                {"id": "bad", "tool": "file.read", "inputs": {}},
                {"id": "good", "tool": "file.list", "inputs": {"path": "."}},
            ],
            "stop_on_error": True,
        }
        res = self.engine.run_task(task)
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["steps"][0]["status"], "failed")
        self.assertEqual(res["steps"][1]["status"], "skipped")

    def test_continue_on_error(self):
        task = {
            "id": "t-continue",
            "steps": [
                {"id": "bad", "tool": "file.read", "inputs": {}},
                {"id": "good", "tool": "file.list", "inputs": {"path": "."}},
            ],
            "stop_on_error": False,
        }
        res = self.engine.run_task(task)
        self.assertEqual(res["status"], "completed_with_errors")
        self.assertEqual(res["steps"][0]["status"], "failed")
        self.assertEqual(res["steps"][1]["status"], "success")

    # -- dry run ----------------------------------------------------------

    def test_dry_run_plans_mutations(self):
        write(self.tmp.name, "hello.txt", "hi")
        task = {
            "id": "t-dry",
            "dry_run": True,
            "steps": [
                {"id": "list", "tool": "file.list",
                 "inputs": {"path": "."}},
                {"id": "write", "tool": "file.write",
                 "inputs": {"path": "out.txt", "content": "should not be written"}},
            ],
            "stop_on_error": True,
        }
        res = self.engine.run_task(task)
        self.assertEqual(res["status"], "planned")
        self.assertEqual(res["steps"][0]["status"], "success")   # read-only runs
        self.assertEqual(res["steps"][1]["status"], "planned")   # mutation planned
        self.assertEqual(len(res["planned_actions"]), 1)
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "out.txt")))

    # -- execution limits -------------------------------------------------

    def test_max_steps_limit(self):
        task = {
            "id": "t-many",
            "max_steps": 2,
            "steps": [
                {"id": "a", "tool": "project.inspect", "inputs": {}},
                {"id": "b", "tool": "project.inspect", "inputs": {}},
                {"id": "c", "tool": "project.inspect", "inputs": {}},
            ],
        }
        ok, errors = self.engine.validate_task(task)
        self.assertFalse(ok)
        res = self.engine.run_task(task)
        self.assertEqual(res["status"], "invalid")

    # -- workspace safety -------------------------------------------------

    def test_workspace_escape_propagates_as_failure(self):
        task = {
            "id": "t-escape",
            "steps": [
                {"id": "s1", "tool": "file.write",
                 "inputs": {"path": "../escape.txt", "content": "x"}},
            ],
            "stop_on_error": True,
        }
        res = self.engine.run_task(task)
        self.assertEqual(res["steps"][0]["status"], "failed")
        self.assertIn("error", res["steps"][0]["result"])
        self.assertFalse(
            os.path.exists(os.path.join(self.tmp.name, "..", "escape.txt")))

    # -- audit trail ------------------------------------------------------

    def test_audit_trail_present_and_serializable(self):
        task = {
            "id": "t-audit",
            "steps": [
                {"id": "s1", "tool": "project.inspect", "inputs": {}},
            ],
        }
        res = self.engine.run_task(task)
        rec = res["steps"][0]
        for key in ("task_id", "id", "tool", "inputs", "result", "status",
                    "started_at", "finished_at", "duration"):
            self.assertIn(key, rec)
        self.assertEqual(rec["task_id"], "t-audit")
        # must be JSON-serializable
        self.assertIsInstance(json.dumps(res), str)
        self.assertIsInstance(json.dumps(res["audit"]), str)


class ModuleLevelRunTaskTests(unittest.TestCase):
    def test_convenience_run_task(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            write(tmp.name, "f.txt", "z")
            res = run_task(
                {"id": "x", "steps": [
                    {"id": "s", "tool": "file.read",
                     "inputs": {"path": "f.txt"}}]},
                workspace_root=tmp.name,
            )
            self.assertEqual(res["status"], "completed")
            self.assertEqual(res["steps"][0]["result"]["content"], "z")
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
