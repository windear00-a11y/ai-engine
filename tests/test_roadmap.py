"""Layer W tests: deterministic roadmap / next-step workflow controller.

Covers (roadmap orchestration only — no project files touched):
* manifest sanity (unique ids, prerequisite ordering)
* full PLAN->IMPLEMENT->TEST->AUDIT->FIX->RE-AUDIT->COMMIT->CHECKPOINT cycle
* approval gating: every approval-required action is surfaced; transitions
  are never executed without an approval record
* fail-closed transitions (unknown slice, wrong state, no pending approval)
* approval recorded, rejection reverts to the origin state
* audit verdict handling (GO vs CRITICAL/HIGH -> fix_required loop)
* state persistence round-trip and corruption -> fail closed
* prerequisite ordering via manifest; committed -> checkpoint -> next slice
* CLI determinism (next/status/init) on temp state files only
* project invariants: production knowledge.db never opened by this module,
  CONTRACT_VERSION stays "1"

All state DBs/files are temporary; the production knowledge DB is only ever
read (invariant assertion), mirroring the other Layer test suites.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..")))

from engine.roadmap import (  # noqa: E402
    ROADMAP_MANIFEST,
    MANIFEST_BY_ID,
    WORKFLOW_SCHEMA_VERSION,
    WorkflowState,
    RoadmapController,
    git_facts,
    commit_present,
    ST_PLAN_READY,
    ST_AWAITING_APPROVAL,
    ST_APPROVED,
    ST_IMPLEMENTED,
    ST_TESTED,
    ST_AUDITED_GO,
    ST_FIX_REQUIRED,
    ST_COMMITTED,
    ST_CHECKPOINTED,
    ST_SKIPPED,
    A_PREPARE_PLAN,
    A_REQUEST_APPROVAL,
    A_WAIT,
    A_IMPLEMENT,
    A_TEST,
    A_AUDIT,
    A_FIX,
    A_COMMIT,
    A_CHECKPOINT,
    A_ASK_HUMAN,
    A_DONE,
)

PROD_KB = os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "database", "knowledge.db")
PROD_KB_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"
PROD_CONTRACT = os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "api", "contract.py")


def _tmp_state():
    return os.path.join(tempfile.mkdtemp(prefix="roadmap_"), "state.json")


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class ManifestTests(unittest.TestCase):
    def test_unique_ids_and_prereq_references(self):
        ids = [s.id for s in ROADMAP_MANIFEST]
        self.assertEqual(len(ids), len(set(ids)))
        for s in ROADMAP_MANIFEST:
            for p in s.prerequisites:
                self.assertIn(p, MANIFEST_BY_ID)

    def test_prerequisites_ordered_first(self):
        ids = {s.id: i for i, s in enumerate(ROADMAP_MANIFEST)}
        for s in ROADMAP_MANIFEST:
            for p in s.prerequisites:
                self.assertLess(ids[p], ids[s.id],
                                f"{p} must precede {s.id} in manifest order")

    def test_remaining_roadmap_shape(self):
        self.assertEqual(
            [s.id for s in ROADMAP_MANIFEST],
            ["6A", "6B", "6C", "6D", "6E", "6F", "7A", "7B", "7C", "8A", "9A"])


class StateMachineTests(unittest.TestCase):
    def full_cycle(self, controller, slice_id):
        """Drive one slice through the full protocol via the controller API."""
        c = controller

        self.assertEqual(c.next_action().slice_id, slice_id)

        # defined -> plan_ready (automatic)
        c.prepare_plan(slice_id)
        a = c.next_action()
        self.assertEqual(a.type, A_REQUEST_APPROVAL)
        self.assertEqual(a.step, "implement")
        self.assertTrue(a.requires_human_approval)

        # awaiting approval -> approve -> implemented
        c.request_approval(slice_id)
        self.assertEqual(c.next_action().type, A_WAIT)
        self.assertTrue(c.next_action().requires_human_approval)
        c.approve(slice_id, operator="tester")
        self.assertEqual(c.next_action().type, A_IMPLEMENT)
        self.assertFalse(c.next_action().requires_human_approval)

        # implement -> test (automatic) -> audit (automatic)
        c.mark_implemented(slice_id)
        self.assertEqual(c.next_action().type, A_TEST)
        c.mark_tested(slice_id, evidence={"suite": 1})
        self.assertEqual(c.next_action().type, A_AUDIT)

    def test_full_cycle_GO(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        self.full_cycle(c, "6A")
        c.mark_audited("6A", "GO", {"critical": 0, "high": 0,
                                    "medium": 0, "low": 0})
        self.assertEqual(c.state.status("6A"), ST_AUDITED_GO)

        # audit GO -> request commit approval (STOP)
        a = c.next_action()
        self.assertEqual((a.type, a.step), (A_REQUEST_APPROVAL, "commit"))
        self.assertTrue(a.requires_human_approval)

        c.request_approval("6A")
        self.assertEqual(c.next_action().type, A_WAIT)
        c.approve("6A", operator="tester")
        self.assertEqual(c.next_action().type, A_COMMIT)
        c.mark_committed("6A", head="deadbeef")
        self.assertEqual(c.state.status("6A"), ST_COMMITTED)
        self.assertEqual(c.state.commit_head("6A"), "deadbeef")

        # committed -> CHECKPOINT (automatic) -> nonce bump, then next slice
        a = c.next_action()
        self.assertEqual(a.type, A_CHECKPOINT)
        self.assertFalse(a.requires_human_approval)
        nonce_before = c.state.checkpoint()[0]
        c.mark_checkpointed("6A")
        self.assertEqual(c.state.status("6A"), ST_CHECKPOINTED)
        self.assertEqual(c.state.checkpoint()[0], nonce_before + 1)
        self.assertEqual(c.next_action().slice_id, "6B")

    def test_audit_with_critical_enters_fix_loop(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        self.full_cycle(c, "6A")
        c.mark_audited("6A", "NO-GO", {"critical": 1, "high": 0})
        self.assertEqual(c.state.status("6A"), ST_FIX_REQUIRED)
        self.assertEqual(c.next_action().type, A_FIX)

        # fix -> back to implemented -> re-test -> re-audit GO
        c.mark_fixed("6A")
        self.assertEqual(c.state.status("6A"), ST_IMPLEMENTED)
        self.assertEqual(c.next_action().type, A_TEST)
        c.mark_tested("6A", evidence={"suite": 1})
        c.mark_audited("6A", "GO", {"critical": 0, "high": 0,
                                    "medium": 1, "low": 3})
        self.assertEqual(c.state.status("6A"), ST_AUDITED_GO)
        self.assertEqual(c.next_action().step, "commit")

    def test_high_severity_alone_blocks_go(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        self.full_cycle(c, "6A")
        c.mark_audited("6A", "GO", {"critical": 0, "high": 2})
        self.assertEqual(c.state.status("6A"), ST_FIX_REQUIRED)

    def test_wait_action_is_human_gated(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        c.prepare_plan("6A")
        c.request_approval("6A")
        a = c.next_action()
        self.assertEqual(a.type, A_WAIT)
        self.assertTrue(a.requires_human_approval)

    def test_done_when_all_checkpointed(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        for s in ROADMAP_MANIFEST:
            sd = c.state.data["slices"][s.id]
            sd["status"] = ST_CHECKPOINTED
        self.assertEqual(c.next_action().type, A_DONE)

    def test_prereq_not_checkpointed_blocks_later_work(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        # 6B cannot be planned while 6A is still active: next action stays 6A
        self.assertEqual(c.next_action().slice_id, "6A")
        # even if 6B status were manually advanced, ordering picks 6A first
        c.state.data["slices"]["6B"]["status"] = ST_TESTED
        self.assertEqual(c.next_action().slice_id, "6A")

    def test_skip_optional_slice_advances_controller(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        # mark every slice up to and incl. 7B checkpointed, then skip 7C.
        for s in ROADMAP_MANIFEST:
            sd = c.state.data["slices"][s.id]
            if s.id in ("6A", "6B", "6C", "6D", "6E", "6F", "7A", "7B"):
                sd["status"] = ST_CHECKPOINTED
        self.assertEqual(c.next_action().slice_id, "7C")  # not yet skipped
        res = c.mark_skipped("7C", operator="op", reason="declined")
        self.assertTrue(res["ok"], res)
        self.assertEqual(c.state.status("7C"), ST_SKIPPED)
        self.assertEqual(c.next_action().slice_id, "8A")  # skips past 7C

    def test_skip_requires_defined_state(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        # cannot skip a slice that has already started work
        c.prepare_plan("9A")
        self.assertFalse(c.mark_skipped("9A")["ok"])
        self.assertEqual(c.state.status("9A"), ST_PLAN_READY)


class FailClosedTests(unittest.TestCase):
    def test_approve_without_request_fails(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        c.prepare_plan("6A")
        res = c.approve("6A")
        self.assertFalse(res["ok"])

    def test_approve_unknown_slice_fails(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        self.assertFalse(c.approve("nope")["ok"])

    def test_request_approval_wrong_state_fails(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        # 6A is 'defined'; request_approval requires plan_ready or audited_go
        self.assertFalse(c.request_approval("6A")["ok"])

    def test_double_pending_approval_fails_closed(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        c.prepare_plan("6A")
        self.assertTrue(c.request_approval("6A")["ok"])
        self.assertFalse(c.request_approval("6A")["ok"])

    def test_markers_require_correct_state(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        self.assertFalse(c.mark_implemented("6A")["ok"])
        self.assertFalse(c.mark_tested("6A")["ok"])
        self.assertFalse(c.mark_audited("6A", "GO")["ok"])
        self.assertFalse(c.mark_committed("6A")["ok"])
        self.assertFalse(c.mark_checkpointed("6A")["ok"])

    def test_mark_committed_requires_commit_approval(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        # mark directly to approved with pending_step=implement then commit:
        self.full_cycle_prepare(c, "6A")
        c.state.data["slices"]["6A"]["status"] = ST_APPROVED
        c.state.data["slices"]["6A"]["pending_step"] = "implement"
        self.assertFalse(c.mark_committed("6A")["ok"])

    def full_cycle_prepare(self, c, slice_id):
        c.prepare_plan(slice_id)
        c.request_approval(slice_id)
        c.approve(slice_id, operator="tester")
        c.mark_implemented(slice_id)
        c.mark_tested(slice_id)
        c.mark_audited(slice_id, "GO", {})

    def test_reject_returns_to_origin(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        c.prepare_plan("6A")
        c.request_approval("6A")
        res = c.approve("6A", decision=False)
        self.assertTrue(res["ok"])
        self.assertEqual(c.state.status("6A"), ST_PLAN_READY)
        # still gated: next action is again a request for approval
        a = c.next_action()
        self.assertEqual(a.type, A_REQUEST_APPROVAL)
        self.assertTrue(a.requires_human_approval)

    def test_reject_on_commit_audit_go(self):
        st = WorkflowState.create()
        c = RoadmapController(state=st)
        self.full_cycle_prepare(c, "6A")
        c.request_approval("6A")  # step=commit
        self.assertEqual(c.state.pending_step("6A"), "commit")
        c.approve("6A", decision=False)
        self.assertEqual(c.state.status("6A"), ST_AUDITED_GO)


class StatePersistenceTests(unittest.TestCase):
    def test_round_trip(self):
        path = _tmp_state()
        st = WorkflowState.create(checkpoint_nonce=7, checkpoint_head="h1")
        st.save(path)
        loaded = WorkflowState.load(path)
        self.assertEqual(loaded.checkpoint(), (7, "h1"))
        self.assertEqual(
            sorted(loaded.data["slices"].keys()),
            sorted(s.id for s in ROADMAP_MANIFEST))

    def test_corrupt_state_fails_closed(self):
        path = _tmp_state()
        with open(path, "w") as f:
            f.write("{ not json")
        with self.assertRaises(ValueError):
            WorkflowState.load(path)

    def test_schema_version_mismatch_fails_closed(self):
        path = _tmp_state()
        st = WorkflowState.create()
        st.save(path)
        with open(path) as f:
            data = json.load(f)
        data["schema_version"] = WORKFLOW_SCHEMA_VERSION + 1
        with open(path, "w") as f:
            json.dump(data, f)
        with self.assertRaises(ValueError):
            WorkflowState.load(path)

    def test_unknown_slice_in_state_fails_closed(self):
        st = WorkflowState.create()
        st.data["slices"]["ZZ"] = {"status": ST_CHECKPOINTED}
        with self.assertRaises(ValueError):
            st._validate()

    def test_missing_state_file_fails_closed(self):
        path = os.path.join(tempfile.mkdtemp(), "missing.json")
        with self.assertRaises(FileNotFoundError):
            WorkflowState.load(path)


class EvidenceTests(unittest.TestCase):
    def test_commit_present_by_prefix(self):
        ev = {"log_lines": [
            "abc123 Some unrelated commit",
            "def456 6A task and step persistence in EngineState",
        ]}
        self.assertTrue(commit_present(ev, "6A task and step persistence"))
        self.assertFalse(commit_present(ev, "6B persistent"))

    def test_commit_present_empty_log(self):
        self.assertFalse(commit_present({}, ROADMAP_MANIFEST[0].commit_message))

    def test_git_facts_read_only_and_shape(self):
        facts = git_facts()
        self.assertIsInstance(facts["dirty"], list)
        self.assertIsInstance(facts["log_lines"], list)
        self.assertIn("head", facts)


class CliTests(unittest.TestCase):
    def _run(self, tmp_state, *args):
        proc = subprocess.run(
            [sys.executable, "-m", "engine.roadmap",
             "--state", tmp_state, *args],
            capture_output=True, text=True, cwd=os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))))
        return proc

    def test_init_then_next_is_deterministic(self):
        tmp = _tmp_state()
        p = self._run(tmp, "init", "--nonce", "3", "--head", "h3",
                      "--force")
        self.assertEqual(p.returncode, 0, p.stderr)
        p2 = self._run(tmp, "next", "--no-git")
        self.assertEqual(p2.returncode, 0, p2.stderr)
        action = json.loads(p2.stdout)
        self.assertEqual(action["type"], A_PREPARE_PLAN)
        self.assertEqual(action["slice_id"], "6A")
        self.assertFalse(action["requires_human_approval"])

    def test_init_refuses_to_clobber_without_force(self):
        tmp = _tmp_state()
        self.assertEqual(self._run(tmp, "init").returncode, 0)
        p = self._run(tmp, "init")
        self.assertEqual(p.returncode, 0)
        self.assertIn("error", p.stdout.lower())
        self.assertIn("--force", p.stdout)

    def test_status_prints_all_slices(self):
        tmp = _tmp_state()
        self._run(tmp, "init", "--force")
        p = self._run(tmp, "status")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("6B", p.stdout)
        self.assertIn("9A", p.stdout)

    def test_approve_cli_writes_state(self):
        tmp = _tmp_state()
        self._run(tmp, "init", "--force")

        def ok(*args):
            p = self._run(tmp, *args)
            self.assertEqual(p.returncode, 0, p.stderr)
            return json.loads(p.stdout)

        ok("prepare", "6A")
        ok("request", "6A")
        ok("approve", "6A", "--operator", "cli-user")
        self.assertEqual(ok("next", "--no-git")["type"], A_IMPLEMENT)
        ok("mark", "6A", "implemented")
        ok("mark", "6A", "tested")
        ok("mark", "6A", "audited", "--verdict", "GO")
        self.assertEqual(ok("next", "--no-git")["step"], "commit")
        ok("request", "6A")
        ok("approve", "6A", "--operator", "cli-user")
        self.assertEqual(ok("next", "--no-git")["type"], A_COMMIT)
        ok("mark", "6A", "committed", "--head", "deadbeef")
        self.assertEqual(ok("next", "--no-git")["type"], A_CHECKPOINT)
        ok("mark", "6A", "checkpointed")
        self.assertEqual(ok("next", "--no-git")["slice_id"], "6B")

    def test_approve_reject_via_cli(self):
        tmp = _tmp_state()
        self._run(tmp, "init", "--force")
        p = self._run(tmp, "approve", "6A", "--reject")
        self.assertEqual(p.returncode, 0, p.stderr)
        res = json.loads(p.stdout)
        self.assertFalse(res["ok"])  # no pending approval, fail closed


class InvariantTests(unittest.TestCase):
    def test_knowledge_db_untouched_by_workflow(self):
        before = _sha(PROD_KB)
        tmp = _tmp_state()
        st = WorkflowState.create()
        st.save(tmp)
        c = RoadmapController(state=st)
        c.prepare_plan("6A")
        c.request_approval("6A")
        c.approve("6A")
        c.mark_implemented("6A")
        c.mark_tested("6A")
        c.mark_audited("6A", "GO", {})
        c.request_approval("6A")
        c.approve("6A")
        c.mark_committed("6A", "h")
        c.mark_checkpointed("6A")
        st.save(tmp)
        self.assertEqual(_sha(PROD_KB), before)
        self.assertEqual(
            _sha(PROD_KB),
            "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91")

    def test_contract_version_untouched(self):
        with open(PROD_CONTRACT, encoding="utf-8") as f:
            self.assertIn('CONTRACT_VERSION = "1"', f.read())


if __name__ == "__main__":
    unittest.main()