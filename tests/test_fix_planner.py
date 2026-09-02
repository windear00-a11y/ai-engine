"""Layer 5B planner-integration tests for deterministic fix proposals.

Verifies that FixProposal[] convert into TaskEngine-compatible read-only steps
(preview only, no write/edit), preserve the approval requirement explicitly,
and remain deterministic without executing anything.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.indexer import ProjectIndex
from tools.verification.parser import parse
from tools.verification.fix_rules import dispatch
from tools.planner.deterministic import DeterministicPlanner
from engine.task_engine import TaskEngine


def write_tree(base, files):
    for rel, content in files.items():
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)


class FixPlannerBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "proj")
        os.makedirs(self.root)
        write_tree(self.root, {
            "pkg/__init__.py": "",
            "pkg/a.py": "import os\ndef foo():\n    return 1\ndef bar():\n    return 2\n",
            "tests/test_a.py": "def test_foo():\n    assert 1==1\n",
        })
        self.idx = ProjectIndex(self.root)
        self.idx.build()
        self.planner = DeterministicPlanner(self.root, db_path=self.idx.db_path)
        self.engine = TaskEngine(workspace_root=self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def proposals_for_line(self, line):
        diags = parse("flake8", {"exit_code": 1, "stdout": line, "stderr": "",
                                 "error": None},
                      workspace_root=self.root, db_path=self.idx.db_path)
        facts = [d for d in diags if d.certainty == "fact"]
        return dispatch(facts, workspace_root=self.root, db_path=self.idx.db_path)


class TestPlanFixes(FixPlannerBase):
    def test_proposal_becomes_task_step(self):
        props = self.proposals_for_line(
            "pkg/a.py:1:1: F401 'os' imported but unused\n")
        self.assertEqual(len(props), 1)
        res = self.planner.plan_fixes(props)
        self.assertEqual(res["planner_status"], "ok")
        task = res["task"]
        # steps: file.read for the file + file.diff preview (grouped per file)
        tools = [s["tool"] for s in task["steps"]]
        self.assertIn("file.read", tools)
        self.assertIn("file.diff", tools)
        # no write/edit/mkdir/rollback
        for forbidden in ("file.write", "file.edit", "file.mkdir",
                          "rollback.operation", "rollback.confirm"):
            self.assertNotIn(forbidden, tools)
        # proposal id recorded
        self.assertEqual(res["proposals"], [props[0].id])

    def test_approval_requirement_preserved(self):
        props = self.proposals_for_line(
            "pkg/a.py:1:1: F401 'os' imported but unused\n")
        res = self.planner.plan_fixes(props)
        # The plan-level approval requirement is explicit
        self.assertTrue(res["approval_required"] is True)
        self.assertEqual(res["proposal_details"][0]["id"], props[0].id)
        self.assertEqual(res["proposal_details"][0]["rule_id"], props[0].rule_id)
        self.assertEqual(
            res["proposal_details"][0]["precondition"]["expected_hash"],
            props[0].precondition["expected_hash"])
        # The diff step carries only executable inputs (path + proposed_content)
        diff_step = [s for s in res["task"]["steps"] if s["tool"] == "file.diff"][0]
        self.assertEqual(set(diff_step["inputs"].keys()),
                         {"path", "proposed_content"})
        # proposed_content is a literal patched string with the import removed
        self.assertIsInstance(diff_step["inputs"]["proposed_content"], str)
        self.assertNotIn("import os", diff_step["inputs"]["proposed_content"])

    def test_deterministic_plan(self):
        props = self.proposals_for_line(
            "pkg/a.py:1:1: F401 'os' imported but unused\n")
        r1 = self.planner.plan_fixes(props)
        r2 = self.planner.plan_fixes(props)
        self.assertEqual(r1["task"]["steps"], r2["task"]["steps"])
        self.assertEqual(r1["proposals"], r2["proposals"])

    def test_no_write_no_execute_no_db_mutation(self):
        import sqlite3
        c = sqlite3.connect(self.idx.db_path)
        before = list(c.execute("SELECT rel_path, sha256 FROM files ORDER BY rel_path"))
        c.close()
        props = self.proposals_for_line(
            "pkg/a.py:1:1: F401 'os' imported but unused\n")
        res = self.planner.plan_fixes(props)
        # Run the read-only task through TaskEngine (dry-ish; all steps read-only)
        outcome = self.engine.run_task(res["task"])
        self.assertEqual(outcome["status"], "completed")
        self.assertEqual(outcome["planned_actions"], [])
        # file unchanged
        with open(os.path.join(self.root, "pkg/a.py")) as f:
            self.assertEqual(f.read(),
                             "import os\ndef foo():\n    return 1\ndef bar():\n    return 2\n")
        c = sqlite3.connect(self.idx.db_path)
        after = list(c.execute("SELECT rel_path, sha256 FROM files ORDER BY rel_path"))
        c.close()
        self.assertEqual(before, after)

    def test_empty_proposals_insufficient(self):
        res = self.planner.plan_fixes([])
        self.assertEqual(res["planner_status"], "insufficient_information")

    def test_single_dict_input(self):
        props = self.proposals_for_line(
            "pkg/a.py:1:1: F401 'os' imported but unused\n")
        single = props[0].as_dict()
        res = self.planner.plan_fixes(single)
        self.assertEqual(res["planner_status"], "ok")
        self.assertEqual(res["proposals"], [single["id"]])

    def _multi_file_proposals(self, n):
        files = {"pkg/__init__.py": "", "tests/test_a.py": "def t():\n    pass\n"}
        lines = ""
        for i in range(n):
            rel = f"pkg/f{i}.py"
            files[rel] = "import os\n\n\ndef foo():\n    return 1\n"
            lines += f"{rel}:1:1: F401 'os' imported but unused\n"
        write_tree(self.root, files)
        idx = ProjectIndex(self.root)
        idx.build()
        diags = [d for d in parse(
            "flake8", {"exit_code": 1, "stdout": lines, "stderr": "", "error": None},
            workspace_root=self.root, db_path=idx.db_path) if d.certainty == "fact"]
        return dispatch(diags, workspace_root=self.root, db_path=idx.db_path)

    def test_one_file_plan_valid(self):
        props = self.proposals_for_line(
            "pkg/a.py:1:1: F401 'os' imported but unused\n")
        res = self.planner.plan_fixes(props)
        self.assertEqual(res["planner_status"], "ok")
        out = self.engine.run_task(res["task"])
        self.assertNotEqual(out["status"], "invalid")
        self.assertEqual(out["status"], "completed")

    def test_three_files_plan_valid(self):
        props = self._multi_file_proposals(3)
        self.assertEqual(len(props), 3)
        res = self.planner.plan_fixes(props)
        self.assertEqual(res["planner_status"], "ok")
        self.assertLessEqual(len(res["task"]["steps"]), res["task"]["max_steps"])
        out = self.engine.run_task(res["task"])
        self.assertNotEqual(out["status"], "invalid")
        self.assertEqual(out["status"], "completed")

    def test_too_many_files_fails_closed(self):
        props = self._multi_file_proposals(4)
        self.assertEqual(len(props), 4)
        res = self.planner.plan_fixes(props)
        self.assertEqual(res["planner_status"], "insufficient_information")
        self.assertNotIn("task", res)
        self.assertEqual(res["proposals"], [p.id for p in props])
        self.assertEqual(len(res["proposal_details"]), 4)
        self.assertIs(res["approval_required"], True)
        self.assertIn("max_steps", res["reason"])

    def test_too_many_files_ok_with_larger_constraint(self):
        props = self._multi_file_proposals(4)
        res = self.planner.plan_fixes(props, constraints={"max_steps": 16})
        self.assertEqual(res["planner_status"], "ok")
        self.assertLessEqual(len(res["task"]["steps"]), res["task"]["max_steps"])
        out = self.engine.run_task(res["task"])
        self.assertNotEqual(out["status"], "invalid")
        self.assertEqual(out["status"], "completed")

    def test_multi_file_deterministic(self):
        props = self._multi_file_proposals(3)
        r1 = self.planner.plan_fixes(props)
        r2 = self.planner.plan_fixes(props)
        self.assertEqual(r1["planner_status"], "ok")
        self.assertEqual([s["id"] for s in r1["task"]["steps"]],
                         [s["id"] for s in r2["task"]["steps"]])
        self.assertEqual(r1["proposals"], r2["proposals"])

    def test_multi_file_no_mutation(self):
        import sqlite3
        props = self._multi_file_proposals(3)
        c = sqlite3.connect(self.idx.db_path)
        before = list(c.execute("SELECT rel_path, sha256 FROM files ORDER BY rel_path"))
        c.close()
        res = self.planner.plan_fixes(props)
        self.assertEqual(res["planner_status"], "ok")
        self.engine.run_task(res["task"])
        with open(os.path.join(self.root, "pkg/a.py")) as f:
            self.assertEqual(f.read(),
                             "import os\ndef foo():\n    return 1\ndef bar():\n    return 2\n")
        c = sqlite3.connect(self.idx.db_path)
        after = list(c.execute("SELECT rel_path, sha256 FROM files ORDER BY rel_path"))
        c.close()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
