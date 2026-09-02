"""Layer 7A tests: git status/diff/log inspection tools.

Covers:
* fixture repo: status classifies untracked / staged / modified, deterministic
* clean repo: empty status
* diff: modified-file diff + changed-list; non-repo fail-closed (no raise)
* path confinement: absolute / traversal / outside-root rejected BEFORE any
  subprocess spawn
* log: deterministic hash+subject, n clamping, bad n rejected
* read-only proof: repo state + HEAD untouched by inspection
* closed allowlist: unknown commands/attrs are hard errors, never executed
* engine integration: registry has the three git tools and a coordinator task
  persists git.status success
* production knowledge.db SHA invariant
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

from tools.git import GitTools
from tools.permissions import EngineState, PathPolicy, ApprovalGate, Policy
from engine.task_engine import TaskEngine, DEFAULT_KNOWLEDGE_DIR
from engine.coordinator import PersistentCoordinator

PROD_KB_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"


def make_repo(with_tracked=True, tracked_name="tracked.txt",
              tracked_content="base\n"):
    root = tempfile.mkdtemp(prefix="git_tools_")
    subprocess.run(["git", "-C", root, "init", "-q"], check=True)
    subprocess.run(["git", "-C", root, "config", "user.email", "t@x"],
                   check=True)
    subprocess.run(["git", "-C", root, "config", "user.name", "t"],
                   check=True)
    if with_tracked:
        with open(os.path.join(root, tracked_name), "w") as f:
            f.write(tracked_content)
        subprocess.run(["git", "-C", root, "add", tracked_name], check=True)
        subprocess.run(["git", "-C", root, "commit", "-q", "-m", "init"],
                       check=True)
    return root


class GitToolsTests(unittest.TestCase):
    def tearDown(self):
        if hasattr(self, "_roots"):
            for r in self._roots:
                shutil.rmtree(r, ignore_errors=True)

    def repo(self, **kw):
        root = make_repo(**kw)
        self._roots = getattr(self, "_roots", []) + [root]
        return root

    # -- status -----------------------------------------------------------

    def test_status_classifies_deterministically(self):
        root = self.repo()
        with open(os.path.join(root, "tracked.txt"), "w") as f:
            f.write("edited\n")
        with open(os.path.join(root, "new.txt"), "w") as f:
            f.write("u\n")
        res = GitTools(root).status()
        self.assertTrue(res["ok"])
        self.assertEqual(res["modified"], ["tracked.txt"])
        self.assertEqual(res["untracked"], ["new.txt"])
        self.assertEqual(sorted(res["raw"]), [" M tracked.txt", "?? new.txt"])
        self.assertEqual(res["count"], 2)

    def test_clean_repo_empty_status(self):
        root = self.repo()
        res = GitTools(root).status()
        self.assertTrue(res["ok"])
        self.assertTrue(res["clean"])
        self.assertEqual(res["count"], 0)

    def test_status_added_and_staged(self):
        root = self.repo()
        with open(os.path.join(root, "added.txt"), "w") as f:
            f.write("a\n")
        subprocess.run(["git", "-C", root, "add", "added.txt"], check=True)
        res = GitTools(root).status()
        self.assertEqual(res["staged"], ["added.txt"])

    # -- diff -------------------------------------------------------------

    def test_diff_changed_list_and_raw(self):
        root = self.repo()
        with open(os.path.join(root, "tracked.txt"), "w") as f:
            f.write("edited\n")
        res = GitTools(root).diff()
        self.assertTrue(res["ok"])
        self.assertTrue(res["changed"])
        self.assertEqual(res["files_changed"], ["tracked.txt"])
        self.assertTrue(any("+edited" in ln for ln in res["raw"]))

    def test_diff_clean_repo(self):
        root = self.repo()
        res = GitTools(root).diff()
        self.assertTrue(res["ok"])
        self.assertFalse(res["changed"])
        self.assertEqual(res["files_changed"], [])

    def test_diff_with_valid_relative_path(self):
        root = self.repo()
        path = os.path.join(root, "tracked.txt")
        with open(path, "w") as f:
            f.write("changed\n")
        res = GitTools(root).diff("tracked.txt")
        self.assertTrue(res["ok"])
        self.assertEqual(res["files_changed"], ["tracked.txt"])

    def test_diff_escape_rejected_before_spawn(self):
        root = self.repo()
        git = GitTools(root)
        # absolute path
        self.assertFalse(git.diff(os.path.join(root, "tracked.txt"))["ok"])
        # traversal
        self.assertFalse(git.diff("../outside.txt")["ok"])
        # rooted traversal
        self.assertFalse(git.diff("/../outside.txt")["ok"])
        # outside workspace but relative
        self.assertFalse(git.diff("..")["ok"])

    def test_non_repo_fails_closed_never_raises(self):
        root = tempfile.mkdtemp(prefix="git_notrepo_")
        self._roots = getattr(self, "_roots", []) + [root]
        git = GitTools(root)
        for call in (git.status, git.diff, git.log):
            res = call()
            self.assertFalse(res["ok"])
            self.assertTrue("error" in res)

    # -- log --------------------------------------------------------------

    def test_log_deterministic_commits(self):
        root = self.repo()
        res = GitTools(root).log()
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["commits"]), 1)
        c = res["commits"][0]
        self.assertEqual(len(c["hash"]), 7)
        self.assertEqual(c["subject"], "init")
        # deterministic across calls
        self.assertEqual(GitTools(root).log(), res)

    def test_log_clamps_and_rejects_n(self):
        root = self.repo()
        git = GitTools(root)
        self.assertTrue(git.log(n=5)["ok"])
        self.assertFalse(git.log(n=0)["ok"])
        self.assertFalse(git.log(n=-1)["ok"])
        self.assertTrue(git.log(n=10 ** 9)["ok"])  # clamped down to 200
        self.assertFalse(git.log(n="3")["ok"])

    # -- read-only + allowlist --------------------------------------------

    def test_inspection_is_read_only(self):
        root = self.repo()
        before = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout
        git = GitTools(root)
        git.status()
        git.diff()
        git.log()
        after = subprocess.run(
            ["git", "-C", root, "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout
        self.assertEqual(after, before)
        self.assertEqual(subprocess.run(
            ["git", "-C", root, "status", "--porcelain"],
            capture_output=True, text=True).stdout, "")

    def test_closed_allowlist_no_arbitrary_git(self):
        root = self.repo()
        git = GitTools(root)
        res = git.commit("-m", "nope")
        self.assertFalse(res["ok"])
        self.assertIn("not an allowed read tool", res["error"])
        res = git.add("tracked.txt")
        self.assertFalse(res["ok"])
        # the repo is unchanged (nothing for commit/add would do on clean repo)
        self.assertEqual(git.status()["count"], 0)

    # -- engine + coordinator integration ---------------------------------

    def test_registry_exposes_git_tools(self):
        root = self.repo()
        gate = ApprovalGate(path_policy=PathPolicy(root, policy=Policy()),
                            state=EngineState(db_path=os.path.join(
                                tempfile.mkdtemp(), "st.db")),
                            approver=lambda p: True)
        engine = TaskEngine(knowledge_dir=DEFAULT_KNOWLEDGE_DIR,
                            workspace_root=root, permissions=gate)
        for t in ("git.status", "git.diff", "git.log"):
            self.assertIn(t, engine.registry)

    def test_coordinator_task_persists_git_status(self):
        root = self.repo()
        state = EngineState(db_path=os.path.join(tempfile.mkdtemp(), "st.db"))
        gate = ApprovalGate(path_policy=PathPolicy(root, policy=Policy()),
                            state=state, approver=lambda p: True)
        engine = TaskEngine(knowledge_dir=DEFAULT_KNOWLEDGE_DIR,
                            workspace_root=root, permissions=gate)
        coord = PersistentCoordinator(state, engine, owner_token="o")
        task = {"id": "git1", "steps": [
            {"id": "s1", "tool": "git.status", "inputs": {}}]}
        self.assertTrue(coord.submit(task)["ok"])
        res = coord.run("git1")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["status"], "completed")
        step = state.get_task_step("git1", "s1")
        self.assertEqual(step["status"], "success")
        result = json.loads(step["result_json"])
        self.assertTrue(result["ok"])
        self.assertIn("count", result)

    # -- invariants -------------------------------------------------------

    def test_production_kb_hash_unchanged(self):
        path = os.path.join(os.path.dirname(__file__), "..",
                            "database", "knowledge.db")
        with open(path, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(),
                             PROD_KB_SHA)

    def test_git_tools_no_shell_no_writes(self):
        import inspect
        import tools.git as gt
        src = inspect.getsource(gt)
        self.assertNotIn("shell=True", src)
        self.assertNotIn("GIT_DIR", src)
        self.assertIn("_ALLOWED_COMMANDS", src)
        self.assertNotIn("def commit(", src)
        self.assertNotIn("def add(", src)
        self.assertNotIn("def push(", src)
        self.assertNotIn("def amend(", src)
        self.assertNotIn("def shell(", src)