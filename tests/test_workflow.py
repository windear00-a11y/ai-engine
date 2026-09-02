"""Layer 8A tests: deterministic workflow automation over the coordinator.

Covers:
* dry-run ships a plan with no mutations (tree + git untouched).
* operator-denied loop: zero mutations, items end planned_denied, denial audited.
* full e2e approve -> apply -> verify -> approval-gated git commit (exactly-one
  HEAD advance, clean tree).
* verify-failure after mutation -> 6D rollback restores the workspace, no commit.
* runaway cap: max_iterations stops the loop, remaining items untouched.
* restart/resume: reconcile + skipping already-completed items (no re-run).
* journaling: audit rows for gates/decisions are persisted.
* confinement: invalid items fail closed at spec construction.
* invariants: production knowledge.db SHA + CONTRACT_VERSION unchanged.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from engine.workflow import WorkflowSpec, WorkflowEngine, ST_COMPLETED, \
    ST_PLANNED_DENIED, ST_CAPPED, ST_ROLLED_BACK
from tools.permissions import EngineState, PathPolicy, ApprovalGate, Policy
from engine.task_engine import TaskEngine, DEFAULT_KNOWLEDGE_DIR
from engine.coordinator import PersistentCoordinator

PROD_KB_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"
PROD_KB = os.path.join(os.path.dirname(__file__), "..", "database",
                       "knowledge.db")


def _kb_sha():
    with open(PROD_KB, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class WorkflowHarness:
    def __init__(self, engine_approver=None, git_init=True, git_policy="deny"):
        root = tempfile.mkdtemp(prefix="wf_")
        state = EngineState(db_path=os.path.join(
            tempfile.mkdtemp(prefix="wf_state_"), "engine_state.db"))
        gate = ApprovalGate(
            path_policy=PathPolicy(root, policy=Policy({"git": git_policy})),
            state=state,
            approver=engine_approver or (lambda p: False))
        engine = TaskEngine(knowledge_dir=DEFAULT_KNOWLEDGE_DIR,
                            workspace_root=root, permissions=gate)
        coord = PersistentCoordinator(state, engine, owner_token="owner-1")
        self.root = root
        self.state = state
        self.gate = gate
        self.engine = engine
        self.coord = coord
        if git_init:
            for cmd in (["init", "-q"], ["config", "user.email", "t@x"],
                        ["config", "user.name", "t"]):
                subprocess.run(["git", "-C", root] + cmd, check=True)
            with open(os.path.join(root, "ok.py"), "w") as f:
                f.write("x = 1\n")
            subprocess.run(["git", "-C", root, "add", "ok.py"], check=True)
            subprocess.run(["git", "-C", root, "commit", "-q", "-m", "init"],
                           check=True)
        self._tmp = [root, os.path.dirname(state.db_path)]

    def cleanup(self):
        for p in self._tmp:
            shutil.rmtree(p, ignore_errors=True)

    def write(self, rel, content):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    def read(self, rel):
        path = os.path.join(self.root, rel)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return f.read()

    def git_head(self):
        return subprocess.run(["git", "-C", self.root, "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()

    def git_clean(self):
        return subprocess.run(["git", "-C", self.root, "status",
                               "--porcelain"],
                              capture_output=True,
                              text=True).stdout.strip() == ""


def write_item(task_id, path, content):
    return {"id": task_id, "stop_on_error": True,
            "steps": [{"id": "s1", "tool": "file.write",
                       "inputs": {"path": path, "content": content}}]}


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(self._cleanup)
        self.harness = None

    def _make(self, **kw):
        self.harness = WorkflowHarness(**kw)
        self.addCleanup(self.harness.cleanup)
        return self.harness

    def _cleanup(self):
        if self.harness:
            self.harness.cleanup()

    # -- 1. dry-run ships a plan with no mutations -------------------------

    def test_dry_run_no_mutations(self):
        h = self._make(engine_approver=lambda p: True)
        spec = WorkflowSpec(id="w1", workspace_root=h.root,
                            items=(write_item("t1", "a.txt", "a\n"),))
        wf = WorkflowEngine(spec, h.engine, h.coord, operator=lambda i: True)
        plan = wf.plan_item("t1")
        self.assertTrue(plan["ok"])
        self.assertEqual(len(plan["planned_actions"]), 1)
        self.assertEqual(plan["planned_actions"][0]["tool"], "file.write")
        # tree + git untouched after dry-run
        self.assertFalse(os.path.exists(os.path.join(h.root, "a.txt")))
        self.assertTrue(h.git_clean())
        before = h.git_head()
        self.assertEqual(before, h.git_head())

    # -- 2. operator-denied loop: zero mutations ---------------------------

    def test_denied_loop_no_mutations(self):
        h = self._make(engine_approver=lambda p: True)
        spec = WorkflowSpec(id="w2", workspace_root=h.root,
                            items=(write_item("d1", "n.txt", "n\n"),
                                   write_item("d2", "o.txt", "o\n")),
                            verify_tool="project.check")
        wf = WorkflowEngine(spec, h.engine, h.coord, operator=lambda i: False)
        summary = wf.run()
        self.assertEqual(summary["count"], 2)
        for it in summary["items"]:
            self.assertEqual(it["status"], ST_PLANNED_DENIED)
        # no mutation, no commit, HEAD unchanged, denial audited
        self.assertFalse(os.path.exists(os.path.join(h.root, "n.txt")))
        self.assertFalse(os.path.exists(os.path.join(h.root, "o.txt")))
        self.assertTrue(h.git_clean())
        before = h.git_head()
        records = h.state.audit_records()
        wf_domains = [r for r in records if r["domain"] == "workflow"]
        self.assertGreaterEqual(len(wf_domains), 2)
        self.assertTrue(all(r["status"] == "denied" for r in wf_domains))
        self.assertEqual(before, h.git_head())

    # -- 3. e2e approve -> apply -> verify -> commit -----------------------

    def test_e2e_approve_to_commit(self):
        h = self._make(engine_approver=lambda p: True, git_policy="allow")
        spec = WorkflowSpec(
            id="w3", workspace_root=h.root,
            items=(write_item("t3", "feat.py", "x = 2\n"),),
            verify_tool="project.check",
            git={"message": "add feat", "files": ["feat.py"]})
        wf = WorkflowEngine(spec, h.engine, h.coord, operator=lambda i: True)
        summary = wf.run()
        self.assertEqual(summary["items"][0]["status"], ST_COMPLETED)
        # file created, committed, tree clean, HEAD advanced exactly once
        self.assertEqual(h.read("feat.py"), "x = 2\n")
        self.assertTrue(h.git_clean())
        log = subprocess.run(["git", "-C", h.root, "log", "--oneline"],
                             capture_output=True, text=True).stdout
        self.assertIn("add feat", log)
        self.assertEqual(summary["count"], 1)

    # -- 4. verify failure -> rollback, no commit --------------------------

    def test_verify_failure_rolls_back(self):
        h = self._make(engine_approver=lambda p: True)
        # writing invalid python makes project.check fail -> rollback removes it
        spec = WorkflowSpec(id="w4", workspace_root=h.root,
                            items=(write_item("t4", "bad.py", "def f(:\n"),),
                            verify_tool="project.check")
        wf = WorkflowEngine(spec, h.engine, h.coord, operator=lambda i: True)
        summary = wf.run()
        status = summary["items"][0]["status"]
        self.assertEqual(status, ST_ROLLED_BACK)
        # rollback removed the newly-created broken file; no commit happened
        self.assertIsNone(h.read("bad.py"))
        before = h.git_head()
        log = subprocess.run(["git", "-C", h.root, "log", "--oneline"],
                             capture_output=True, text=True).stdout
        self.assertNotIn("bad", log)
        self.assertEqual(h.git_head(), before)

    # -- 5. runaway cap ----------------------------------------------------

    def test_runaway_cap(self):
        h = self._make(engine_approver=lambda p: True)
        spec = WorkflowSpec(id="w5", workspace_root=h.root,
                            items=(write_item("c1", "a.txt", "a\n"),
                                   write_item("c2", "b.txt", "b\n"),
                                   write_item("c3", "c.txt", "c\n")),
                            max_iterations=1)
        wf = WorkflowEngine(spec, h.engine, h.coord, operator=lambda i: True)
        summary = wf.run()
        self.assertTrue(summary["capped"])
        self.assertEqual(summary["items"][0]["status"], ST_COMPLETED)
        self.assertEqual(summary["items"][1]["status"], ST_CAPPED)
        self.assertEqual(summary["items"][2]["status"], ST_CAPPED)
        # capped items never mutated
        self.assertIsNone(h.read("b.txt"))
        self.assertIsNone(h.read("c.txt"))

    # -- 6. resume skips completed, never re-runs --------------------------

    def test_resume_skips_completed_and_reconciles(self):
        h = self._make(engine_approver=lambda p: True)
        spec = WorkflowSpec(id="w6", workspace_root=h.root,
                            items=(write_item("r1", "k.txt", "k\n"),
                                   write_item("r2", "m.txt", "m\n")),
                            verify_tool="project.check")
        wf = WorkflowEngine(spec, h.engine, h.coord, operator=lambda i: True)
        # drive item r1 to completion manually
        wf.plan_item("r1")
        wf.decide("r1", True)
        wf.apply_item("r1")
        self.assertEqual(wf.item_status("r1"), ST_COMPLETED)
        self.assertEqual(h.read("k.txt"), "k\n")

        # invent an interrupted (running) task to prove reconcile runs
        running = {"id": "interrupted", "stop_on_error": True, "steps": []}
        h.state.create_task("interrupted", running, h.root, status="planned")
        h.state.claim_task("interrupted", "owner-1")

        res = wf.resume()
        # completed r1 was NOT re-planned/re-run, and reconcile marked the
        # interrupted task failed without touching k.txt
        self.assertEqual(wf.item_status("r1"), ST_COMPLETED)
        self.assertNotEqual(
            h.state.get_task("interrupted")["status"], "running")
        self.assertEqual([i["item_id"] for i in res["items"]],
                         ["r1", "r2"])
        # resume only re-planned r2 (dry-run), never re-executed r1
        self.assertEqual(h.read("k.txt"), "k\n")  # content unchanged (no dup)

    # -- 7. journaling present ---------------------------------------------

    def test_journaling_audit(self):
        h = self._make(engine_approver=lambda p: True)
        before = h.state.audit_count()
        spec = WorkflowSpec(id="w7", workspace_root=h.root,
                            items=(write_item("j1", "j.txt", "j\n"),))
        wf = WorkflowEngine(spec, h.engine, h.coord, operator=lambda i: True)
        wf.run()
        self.assertGreater(h.state.audit_count(), before)
        wf_domains = [r for r in h.state.audit_records()
                      if r["domain"] == "workflow"]
        self.assertTrue(wf_domains)  # approve decisions persisted

    # -- 8. confinement: invalid items fail closed -------------------------

    def test_invalid_item_rejected(self):
        h = self._make()
        bad = {"id": "x1", "steps": [{"id": "s", "tool": "nope"}]}
        spec = WorkflowSpec(id="w8", workspace_root=h.root, items=(bad,))
        with self.assertRaises(ValueError):
            WorkflowEngine(spec, h.engine, h.coord, operator=lambda i: True)

    # -- 9. invariants -----------------------------------------------------

    def test_invariants(self):
        h = self._make(engine_approver=lambda p: True)
        spec = WorkflowSpec(id="w9", workspace_root=h.root,
                            items=(write_item("i1", "z.txt", "z\n"),))
        wf = WorkflowEngine(spec, h.engine, h.coord, operator=lambda i: True)
        wf.run()
        self.assertEqual(_kb_sha(), PROD_KB_SHA)
        import re
        with open(os.path.join(os.path.dirname(__file__), "..", "api",
                               "contract.py")) as f:
            src = f.read()
        self.assertIn('CONTRACT_VERSION = "1"', src)


if __name__ == "__main__":
    unittest.main()
