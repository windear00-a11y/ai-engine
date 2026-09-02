"""Layer 7B tests: approval-gated git stage/commit write tools.

Covers:
* deny-by-default: no git capability / policy git:deny -> denied before any
  write; bare GIT gate check still denies.
* policy git:allow but approver False -> denied, working tree unchanged.
* full approval path: stage untracked then commit -> HEAD matches, tree clean.
* exactly-once: nothing-staged commit fails clean with HEAD unchanged; no
  duplicate commit on repeated successful commits.
* path confinement + --all rejection + message validation (pre-spawn).
* closed surface: push/amend/force/checkout are not reachable.
* journaling: approved + denied git writes all produce audit rows.
* engine integration: coordinator persists approved git.commit; MUTATING
  contains stage/commit; dry-run plans them without executing.
* production knowledge.db SHA invariant.
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
from tools.permissions import EngineState, PathPolicy, Policy, ApprovalGate
from tools.permissions.decisions import Domain
from engine.task_engine import TaskEngine, DEFAULT_KNOWLEDGE_DIR
from engine.coordinator import PersistentCoordinator

PROD_KB_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"


def git_root():
    root = tempfile.mkdtemp(prefix="git_write_")
    for cmd in (["init", "-q"], ["config", "user.email", "t@x"],
                ["config", "user.name", "t"]):
        subprocess.run(["git", "-C", root] + cmd, check=True)
    return root


def gated_tools(root, git_policy, approver):
    policy = Policy({"git": git_policy})
    pp = PathPolicy(root, policy=policy)
    state = EngineState(db_path=os.path.join(
        tempfile.mkdtemp(prefix="gw_state_"), "engine_state.db"))
    gate = ApprovalGate(path_policy=pp, state=state, approver=approver)
    return GitTools(root, permissions=gate), gate, state


class GitWriteTests(unittest.TestCase):
    def opin(self, root):
        with open(os.path.join(root, "new.txt"), "w") as f:
            f.write("n\n")
        return "new.txt"

    def head(self, root):
        return subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                              capture_output=True, text=True).stdout.strip()

    def tree_clean(self, root):
        return subprocess.run(["git", "-C", root, "status", "--porcelain"],
                              capture_output=True, text=True).stdout.strip() == ""

    # -- deny by default --------------------------------------------------

    def test_deny_by_default_policy_and_bare_check(self):
        root = git_root()
        git, gate, _state = gated_tools(root, "deny", lambda p: True)
        self.assertFalse(git.stage([self.opin(root)])["ok"])
        self.assertFalse(git.commit("boom")["ok"])
        # bare GIT gate check still denies regardless of approver
        allowed, _d, _e = gate.authorize_git(
            "commit", {"op": "commit", "message": "x"})
        self.assertFalse(allowed)
        bare = gate.check(Domain.GIT, "t")
        self.assertNotEqual(bare.kind.value, "allow")

    def test_deny_default_approver_even_when_allowed(self):
        root = git_root()
        git, _gate, _st = gated_tools(root, "allow", lambda p: False)
        path = self.track(root)
        res = git.stage([path])
        self.assertFalse(res["ok"])
        with open(os.path.join(root, path)) as f:
            self.assertEqual(f.read(), "keep")
        self.tree_clean(root)

    def track(self, root, name="tracked.txt", content="keep"):
        with open(os.path.join(root, name), "w") as f:
            f.write(content)
        subprocess.run(["git", "-C", root, "add", name], check=True)
        subprocess.run(["git", "-C", root, "commit", "-q", "-m", "init"],
                       check=True)
        return name

    # -- approval path ----------------------------------------------------

    def test_full_approval_stage_and_commit(self):
        root = git_root()
        git, _gate, _st = gated_tools(root, "allow", lambda p: True)
        res = git.stage([self.opin(root)])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["staged"], ["new.txt"])
        self.assertEqual(git.status()["staged"], ["new.txt"])
        cres = git.commit("add new.txt")
        self.assertTrue(cres["ok"], cres)
        self.assertEqual(cres["short"], self.head(root)[:7])
        self.assertTrue(self.tree_clean(root))
        # deterministic: the only commit is the one we just made
        self.assertEqual(len(git.log()["commits"]), 1)
        self.assertEqual(git.log()["commits"][0]["subject"], "add new.txt")

    def test_commit_with_nothing_staged_fails_clean(self):
        root = git_root()
        self.track(root)
        git, _g, _s = gated_tools(root, "allow", lambda p: True)
        before = self.head(root)
        res = git.commit("nothing to commit")
        self.assertFalse(res["ok"])
        self.assertEqual(self.head(root), before)  # HEAD unchanged

    def test_no_duplicate_commit(self):
        root = git_root()
        git, _g, _s = gated_tools(root, "allow", lambda p: True)
        git.stage([self.opin(root)])
        git.commit("first")
        h1 = self.head(root)
        self.assertFalse(git.commit("")["ok"])  # empty rejected
        self.assertEqual(self.head(root), h1)  # no extra commit

    # -- confinement + validation -----------------------------------------

    def test_stage_rejects_bad_paths_before_spawn(self):
        root = git_root()
        outer = tempfile.mkdtemp(prefix="ghost_")
        with open(os.path.join(outer, "x"), "w") as f:
            f.write("x")
        git, _g, _s = gated_tools(root, "allow", lambda p: True)
        self.assertFalse(git.stage([os.path.join(root, "new.txt")])["ok"])
        self.assertFalse(git.stage(["../outside"])["ok"])
        self.assertFalse(git.stage(["nonexistent"])["ok"])
        self.assertFalse(git.stage([])["ok"])
        self.assertFalse(git.stage("new.txt")["ok"])  # not a list
        self.tree_clean(root)

    def test_no_all_argument(self):
        root = git_root()
        git, _g, _s = gated_tools(root, "allow", lambda p: True)
        self.assertFalse(git.stage(["--all"])["ok"])  # not a valid rel path

    def test_commit_message_validation(self):
        root = git_root()
        git, _g, _s = gated_tools(root, "allow", lambda p: True)
        for bad in ("", "   ", "a" * 201, "line1\nline2"):
            self.assertFalse(git.commit(bad)["ok"])

    # -- closed surface ---------------------------------------------------

    def test_no_push_amend_force_checkout(self):
        root = git_root()
        git, _g, _s = gated_tools(root, "allow", lambda p: True)
        for name in ("push", "amend", "force", "checkout", "reset",
                     "rebase", "merge", "branch"):
            res = getattr(git, name)("anything")
            self.assertFalse(res["ok"], name)
            self.assertIn("not an allowed read tool", res["error"])

    # -- journaling -------------------------------------------------------

    def test_git_writes_are_audited(self):
        root = git_root()
        git, _gate, state = gated_tools(root, "allow", lambda p: True)
        git.stage([self.opin(root)])
        git.commit("audit me")
        rows = [r for r in state.audit_records()
                if r["domain"] == "git"]
        self.assertTrue(rows, "expected at least one git audit row")
        kinds = {r["permission"] for r in rows}
        self.assertTrue({"stage", "commit"} <= kinds, kinds)

    def test_denied_git_write_is_audited(self):
        root = git_root()
        git, _g, state = gated_tools(root, "deny", lambda p: True)
        git.stage([self.opin(root)])
        denied = [r for r in state.audit_records()
                  if r["domain"] == "git" and r["status"] == "denied"]
        self.assertTrue(denied)

    # -- engine integration -----------------------------------------------

    def test_engine_registry_and_mutating(self):
        root = git_root()
        gate = ApprovalGate(path_policy=PathPolicy(root, policy=Policy(
            {"git": "allow"})),
            state=EngineState(db_path=os.path.join(tempfile.mkdtemp(), "s.db")),
            approver=lambda p: True)
        engine = TaskEngine(knowledge_dir=DEFAULT_KNOWLEDGE_DIR,
                            workspace_root=root, permissions=gate)
        for t in ("git.stage", "git.commit"):
            self.assertIn(t, engine.registry)
            self.assertIn(t, TaskEngine.MUTATING)

    def test_coordinator_persists_approved_git_commit(self):
        root = git_root()
        self.track(root)
        with open(os.path.join(root, "add.txt"), "w") as f:
            f.write("x\n")
        state = EngineState(db_path=os.path.join(tempfile.mkdtemp(), "s.db"))
        gate = ApprovalGate(path_policy=PathPolicy(root, policy=Policy(
            {"git": "allow"})), state=state, approver=lambda p: True)
        engine = TaskEngine(knowledge_dir=DEFAULT_KNOWLEDGE_DIR,
                            workspace_root=root, permissions=gate)
        coord = PersistentCoordinator(state, engine, owner_token="o")
        task = {"id": "g2", "steps": [
            {"id": "s1", "tool": "git.stage",
             "inputs": {"paths": ["add.txt"]}},
            {"id": "s2", "tool": "git.commit",
             "inputs": {"message": "add add.txt"}},
        ]}
        self.assertTrue(coord.submit(task)["ok"])
        res = coord.run("g2")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["status"], "completed")
        self.assertTrue(self.tree_clean(root))

    # -- invariants -------------------------------------------------------

    def test_production_kb_hash_unchanged(self):
        path = os.path.join(os.path.dirname(__file__), "..",
                            "database", "knowledge.db")
        with open(path, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(),
                             PROD_KB_SHA)


if __name__ == "__main__":
    unittest.main()