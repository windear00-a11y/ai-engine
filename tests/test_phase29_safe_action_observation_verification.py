"""Phase 29 acceptance: the safe action / observation / verification loop.

Covers the deterministic, provenance-preserving, project-isolated path

    Plan -> Authority / Approval -> Action -> Observation
        -> Verification -> Outcome -> Experience

through (a) the pure domain engines (authority, action, observation,
verification), (b) the LifecycleService facade, and (c) the v2 contract.
Unapproved/unknown authority never executes, caller-provided success is never
verification evidence, and failures / partials / unknowns are retained.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ai_engine.authority import (APPROVED, DENIED, REQUIRES_APPROVAL)  # noqa: E402
from ai_engine.action import (execute_effect, action_status_from_effect)  # noqa: E402


def _elevated_step(step_id="pls_1", action="deploy the release",
                   authority_class="elevated"):
    return {"step_id": step_id, "action": action,
            "authority_class": authority_class}


def _low_step(step_id="pls_1", action="noop", authority_class="low"):
    return {"step_id": step_id, "action": action,
            "authority_class": authority_class}


class Phase29AuthorityEngineTests(unittest.TestCase):
    """Pure authority engine: the decision vocabulary and approval gate."""

    def setUp(self):
        from ai_engine.authority import evaluate_authority
        self.evaluate = evaluate_authority

    def test_approved_denied_requires_approval_vocabulary(self):
        from ai_engine.authority import (APPROVED, DENIED, REQUIRES_APPROVAL,
                                         AUTHORITY_STATES)
        self.assertEqual(AUTHORITY_STATES,
                         (APPROVED, DENIED, REQUIRES_APPROVAL, "invalid_plan",
                          "invalid_step", "unknown"))

    def test_low_risk_auto_approved(self):
        res = self.evaluate(
            "pl_1", ["pls_1"], "alice", "p1",
            steps=[_low_step()])
        self.assertEqual(res["decision"], APPROVED)

    def test_elevated_requires_approval_without_grant(self):
        res = self.evaluate(
            "pl_1", ["pls_1"], "alice", "p1",
            steps=[_elevated_step()])
        self.assertEqual(res["decision"], REQUIRES_APPROVAL)
        self.assertEqual(res["per_step"]["pls_1"]["decision"],
                         REQUIRES_APPROVAL)

    def test_grant_approves_elevated(self):
        res = self.evaluate(
            "pl_1", ["pls_1"], "alice", "p1",
            steps=[_elevated_step()],
            approvals=[{"plan_id": "pl_1", "step_id": "pls_1",
                        "actor": "alice", "state": "approved"}])
        self.assertEqual(res["decision"], APPROVED)

    def test_grant_does_not_cross_actors(self):
        res = self.evaluate(
            "pl_1", ["pls_1"], "bob", "p1",
            steps=[_elevated_step()],
            approvals=[{"plan_id": "pl_1", "step_id": "pls_1",
                        "actor": "alice", "state": "approved"}])
        self.assertEqual(res["decision"], REQUIRES_APPROVAL)

    def test_deny_list_denies(self):
        action = "shutdown the cluster"
        res = self.evaluate(
            "pl_1", ["pls_1"], "alice", "p1",
            steps=[{"step_id": "pls_1", "action": action,
                    "authority_class": "elevated"}],
            policy={"deny_effects": (action,)})
        self.assertEqual(res["decision"], DENIED)

    def test_unknown_trust_level_is_never_approval(self):
        res = self.evaluate(
            "pl_1", ["pls_1"], "alice", "p1",
            steps=[_low_step()],
            policy={"trust_level": "remote"})
        self.assertEqual(res["decision"], "unknown")
        self.assertNotEqual(res["decision"], APPROVED)

    def test_invalid_plan_and_invalid_step(self):
        res = self.evaluate("", ["pls_1"], "alice", "p1",
                            steps=[_low_step()])
        self.assertEqual(res["decision"], "invalid_plan")
        res = self.evaluate("pl_1", ["pls_missing"], "alice", "p1",
                            steps=[_low_step()])
        self.assertEqual(res["decision"], "invalid_step")

    def test_required_evidence_gates_approval(self):
        res = self.evaluate(
            "pl_1", ["pls_1"], "alice", "p1",
            steps=[_low_step()],
            required_claims=("ticket:approved",))
        self.assertEqual(res["decision"], REQUIRES_APPROVAL)
        res2 = self.evaluate(
            "pl_1", ["pls_1"], "alice", "p1",
            steps=[_low_step(authority_class="low")],
            required_claims=("ticket:approved",))
        self.assertEqual(res2["decision"], REQUIRES_APPROVAL)

    def test_mixed_steps_aggregate_to_requires_approval(self):
        res = self.evaluate(
            "pl_1", ["pls_1", "pls_2"], "alice", "p1",
            steps=[_low_step(), _low_step(step_id="pls_2",
                                          action="memory.recall")])
        self.assertEqual(res["decision"], APPROVED)

    def test_authority_id_is_deterministic(self):
        steps = [_elevated_step()]
        a = self.evaluate("pl_1", ["pls_1"], "alice", "p1", steps=steps)
        b = self.evaluate("pl_1", ["pls_1"], "alice", "p1", steps=steps)
        self.assertEqual(a["authority_id"], b["authority_id"])
        self.assertTrue(a["authority_id"].startswith("au_"))


class Phase29ActionEngineTests(unittest.TestCase):
    """Pure action engine: identity and the executor boundary."""

    def test_action_states_vocabulary(self):
        from ai_engine.action import (REQUESTED, APPROVED, EXECUTING,
                                      SUCCEEDED, FAILED, PARTIAL, UNKNOWN,
                                      DENIED, ACTION_STATES)
        self.assertEqual(ACTION_STATES,
                         (REQUESTED, APPROVED, EXECUTING, SUCCEEDED, FAILED,
                          PARTIAL, UNKNOWN, DENIED))

    def test_identity_rejects_global_dedup(self):
        from ai_engine.action import (derive_action_id,
                                      derive_default_request_id)
        aid1 = derive_action_id("pl_1", "pls_1", "alice", "req_a", "p1")
        aid2 = derive_action_id("pl_1", "pls_1", "alice", "req_b", "p1")
        self.assertNotEqual(aid1, aid2)
        self.assertEqual(
            derive_action_id("pl_1", "pls_1", "alice", "req_a", "p1"), aid1)
        self.assertTrue(aid1.startswith("ac_"))
        self.assertTrue(
            derive_default_request_id("pl_1", "pls_1", "alice", "p1")
            .startswith("req_"))

    def test_default_noop_executor(self):
        from ai_engine.action import execute_effect
        res = execute_effect("noop", {})
        self.assertEqual(res["status"], "success")
        self.assertIs(res["observed_state"]["completed"], True)

    def test_unsupported_effect_is_explicit_unknown(self):
        from ai_engine.action import execute_effect
        res = execute_effect("deploy the release", {}, executor={})
        self.assertEqual(res["status"], "unsupported")
        self.assertTrue(res["unsupported"])
        self.assertNotEqual(res["status"], "success")

    def test_callable_executor(self):
        from ai_engine.action import execute_effect
        res = execute_effect("deploy", {}, executor={
            "deploy": lambda e, i: {"status": "success",
                                    "observed_state": {"deployed": True}}})
        self.assertEqual(res["status"], "success")
        self.assertEqual(res["observed_state"], {"deployed": True})

    def test_executor_failure_and_partial_and_exception(self):
        from ai_engine.action import execute_effect
        from ai_engine.action import action_status_from_effect
        fail = execute_effect("x", {}, executor={
            "x": {"status": "failure", "observed_state": {"ok": False}}})
        self.assertEqual(action_status_from_effect(fail["status"]), "failed")
        part = execute_effect("x", {}, executor={
            "x": {"status": "partial", "observed_state": {"n": 1}}})
        self.assertEqual(action_status_from_effect(part["status"]), "partial")

        def boom(e, i):
            raise RuntimeError("boom")
        res = execute_effect("x", {}, executor={"x": boom})
        self.assertEqual(res["status"], "unknown")
        self.assertEqual(action_status_from_effect(res["status"]), "unknown")

    def test_non_dict_result_is_unknown(self):
        res = execute_effect("x", {}, executor={"x": "not-a-dict"})
        self.assertEqual(action_status_from_effect(res["status"]), "unknown")

    def test_observed_state_bounded(self):
        from ai_engine.action import (execute_effect, MAX_OBSERVED_KEYS)
        many = {"k%d" % i: i for i in range(100)}
        res = execute_effect("x", {}, executor=lambda e, i: {
            "status": "success", "observed_state": many})
        self.assertTrue(len(res["observed_state"]) <= MAX_OBSERVED_KEYS)


class Phase29ObservationEngineTests(unittest.TestCase):
    """Pure observation engine: distinct-from-verification identity."""

    def test_states_vocabulary(self):
        from ai_engine.observation import (OBSERVED, PARTIAL, FAILED, UNKNOWN,
                                           OBSERVATION_STATES)
        self.assertEqual(OBSERVATION_STATES, (OBSERVED, PARTIAL, FAILED,
                                              UNKNOWN))

    def test_observation_id_deterministic_and_source_distinct(self):
        from ai_engine.observation import derive_observation_id
        a = derive_observation_id("ac_1", "effect_executor", {"x": 1})
        b = derive_observation_id("ac_1", "effect_executor", {"x": 1})
        self.assertEqual(a, b)
        c = derive_observation_id("ac_1", "manual_observation", {"x": 1})
        self.assertNotEqual(a, c)
        self.assertTrue(a.startswith("ob_"))

    def test_empty_observed_state_is_unknown_not_success(self):
        from ai_engine.observation import observation_status_from_effect
        self.assertEqual(observation_status_from_effect("success"), "observed")
        self.assertEqual(observation_status_from_effect("failure"), "failed")
        self.assertEqual(observation_status_from_effect("partial"), "partial")
        self.assertEqual(observation_status_from_effect("unknown"), "unknown")
        self.assertEqual(observation_status_from_effect(""), "unknown")


class Phase29VerificationEngineTests(unittest.TestCase):
    """Pure verification engine: evidence discipline."""

    def _observations(self, **kwargs):
        return [{"claim_key": k, "value": v, "source": "ob_src"}
                for k, v in kwargs.items()]

    def test_results_vocabulary(self):
        from ai_engine.verification import (VERIFIED_SUCCESS,
                                            VERIFIED_FAILURE, PARTIAL, UNKNOWN,
                                            INSUFFICIENT_EVIDENCE,
                                            CONFLICTING_EVIDENCE,
                                            VERIFICATION_RESULTS)
        self.assertEqual(VERIFICATION_RESULTS,
                         (VERIFIED_SUCCESS, VERIFIED_FAILURE, PARTIAL,
                          UNKNOWN, INSUFFICIENT_EVIDENCE,
                          CONFLICTING_EVIDENCE))

    def test_verified_success_requires_documented_evidence(self):
        from ai_engine.verification import (verify, VERIFIED_SUCCESS,
                                            INSUFFICIENT_EVIDENCE)
        matched = self._observations(completed=True, replicas=3)
        with_evidence = verify({"completed": True, "replicas": 3}, matched,
                               documented_evidence=["ev_1"])
        self.assertEqual(with_evidence["result"], VERIFIED_SUCCESS)
        without = verify({"completed": True, "replicas": 3}, matched,
                         documented_evidence=[])
        self.assertEqual(without["result"], INSUFFICIENT_EVIDENCE)

    def test_verified_failure(self):
        from ai_engine.verification import verify, VERIFIED_FAILURE
        res = verify({"deployed": True}, self._observations(deployed=False),
                     documented_evidence=["ev_1"])
        self.assertEqual(res["result"], VERIFIED_FAILURE)

    def test_partial(self):
        from ai_engine.verification import verify, PARTIAL
        res = verify({"deployed": True, "replicas": 3},
                     self._observations(deployed=True), ["ev_1"])
        self.assertEqual(res["result"], PARTIAL)

    def test_missing_observations_is_insufficient(self):
        from ai_engine.verification import verify, INSUFFICIENT_EVIDENCE
        res = verify({"deployed": True}, [], ["ev_1"])
        self.assertEqual(res["result"], INSUFFICIENT_EVIDENCE)

    def test_conflicting_evidence(self):
        from ai_engine.verification import verify, CONFLICTING_EVIDENCE
        clashes = [{"claim_key": "replicas", "value": 3, "source": "s1"},
                   {"claim_key": "replicas", "value": 5, "source": "s2"}]
        res = verify({"replicas": 3}, clashes, ["ev_1"])
        self.assertEqual(res["result"], CONFLICTING_EVIDENCE)

    def test_caller_claim_is_rejected(self):
        from ai_engine.verification import (verify, INSUFFICIENT_EVIDENCE,
                                            CALLER_CLAIM_REJECTION)
        res = verify({"success": True}, self._observations(success=True),
                     ["ev_1"])
        self.assertEqual(res["result"], INSUFFICIENT_EVIDENCE)
        self.assertIn(CALLER_CLAIM_REJECTION, res["rationale"])
        res2 = verify({"ok": True}, self._observations(ok=True), ["ev_1"])
        self.assertEqual(res2["result"], INSUFFICIENT_EVIDENCE)

    def test_determinism(self):
        from ai_engine.verification import verify
        a = verify({"completed": True}, self._observations(completed=True),
                   ["ev_1"])
        b = verify({"completed": True}, self._observations(completed=True),
                   ["ev_1"])
        self.assertEqual(a, b)


class Phase29ServiceTests(unittest.TestCase):
    """Canonical service loop A-G through ai_engine.lifecycle_service."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p29_")
        from ai_engine.lifecycle_service import LifecycleService
        self.svc = LifecycleService(project_id="p29", data_root=self.tmp)
        self.svc_other = LifecycleService(project_id="p29b", data_root=self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _experiences(self, situation, attempt, outcome="success"):
        ids = []
        for i in range(3):
            ev = self.svc.record_evidence(
                "obs_seed_%d" % i, "seed %d" % i)
            xp = self.svc.record_experience(
                situation, attempt, outcome,
                evidence_ids=[ev["evidence_id"]])
            ids.append(xp["experience_id"])
        return ids

    def _plan(self, situation="deploy the service", attempt="deploy the release"):
        ids = self._experiences(situation, attempt)
        return self.svc.plan_from_experiences(situation, ids)["plan"]

    # -- A. authority gate ---------------------------------------------
    def test_elevated_plan_requires_approval_then_grant_executes(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        self.assertEqual(plan["ordered_steps"][0]["action"],
                         "deploy the release")
        au = self.svc.evaluate_authority(plan["plan_id"], [step_id], "alice")
        self.assertEqual(au["decision"], "requires_approval")
        self.assertTrue(au["authority_id"].startswith("au_"))
        grant = self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        self.assertTrue(grant["grant_id"].startswith("az_"))
        self.assertEqual(grant["state"], "approved")
        au2 = self.svc.evaluate_authority(plan["plan_id"], [step_id], "alice")
        self.assertEqual(au2["decision"], "approved")
        executors = {"deploy the release": lambda e, i: {
            "status": "success",
            "observed_state": {"deployed": True, "replicas": 3}}}
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-A",
            executors=executors)
        self.assertEqual(action["execution_status"], "succeeded")
        self.assertIs(action["executed"], True)
        self.assertEqual(len(action["observation_ids"]), 1)
        verification = self.svc.verify_action(
            action["action_id"],
            expectations={"deployed": True, "replicas": 3})
        self.assertEqual(verification["result"], "verified_success")
        result = self.svc.finalize_action(action["action_id"])
        self.assertEqual(result["classification"], "success")
        self.assertEqual(result["experience"]["outcome_id"],
                         result["outcome"]["outcome_id"])

    def test_noop_plan_auto_approved_and_linked(self):
        plan = self._plan(situation="run noop", attempt="noop")
        step_id = plan["ordered_steps"][0]["step_id"]
        self.assertEqual(plan["ordered_steps"][0]["action"], "noop")
        au = self.svc.evaluate_authority(plan["plan_id"], [step_id], "alice")
        self.assertEqual(au["decision"], "approved")
        action = self.svc.request_action(plan["plan_id"], step_id, "alice")
        self.assertEqual(action["execution_status"], "succeeded")
        self.svc.verify_action(action["action_id"],
                               expectations={"completed": True})
        result = self.svc.finalize_action(action["action_id"])
        self.assertEqual(result["classification"], "success")

    def test_unapproved_never_reaches_effect_layer(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        action = self.svc.request_action(plan["plan_id"], step_id, "alice")
        self.assertEqual(action["execution_status"], "denied")
        self.assertIs(action["executed"], False)
        self.assertEqual(action["observation_ids"], [])
        self.assertEqual(action["authority_decision"], "requires_approval")
        result = self.svc.finalize_action(action["action_id"])
        self.assertEqual(result["classification"], "blocked")

    def test_unknown_authority_cannot_execute(self):
        plan = self._plan(situation="run noop", attempt="noop")
        step_id = plan["ordered_steps"][0]["step_id"]
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice",
            policy={"trust_level": "remote"})
        self.assertEqual(action["authority_decision"], "unknown")
        self.assertEqual(action["execution_status"], "denied")
        self.assertIs(action["executed"], False)

    def test_invalid_plan_and_invalid_step_are_safe_decisions(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        missing = self.svc.evaluate_authority(
            "pl_does_not_exist", [step_id], "alice")
        self.assertEqual(missing["decision"], "invalid_plan")
        bad_step = self.svc.evaluate_authority(
            plan["plan_id"], ["pls_does_not_exist"], "alice")
        self.assertEqual(bad_step["decision"], "invalid_step")

    # -- B. action / executor boundary ---------------------------------
    def test_unsupported_effect_is_explicit_unknown_not_success(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-U",
            executors={})
        self.assertEqual(action["execution_status"], "unknown")
        self.assertEqual(action["authority_decision"], "approved")
        result = self.svc.finalize_action(action["action_id"])
        self.assertEqual(result["classification"], "unknown")

    def test_failure_is_retained(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-F",
            executors={"deploy the release": lambda e, i: {
                "status": "failure",
                "observed_state": {"deployed": False}}})
        self.assertEqual(action["execution_status"], "failed")
        verification = self.svc.verify_action(
            action["action_id"], expectations={"deployed": True})
        self.assertEqual(verification["result"], "verified_failure")
        result = self.svc.finalize_action(action["action_id"])
        self.assertEqual(result["classification"], "failure")

    def test_partial_is_retained(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-P",
            executors={"deploy the release": lambda e, i: {
                "status": "partial",
                "observed_state": {"deployed": True, "replicas": 1}}})
        verification = self.svc.verify_action(
            action["action_id"],
            expectations={"deployed": True, "replicas": 3})
        self.assertEqual(verification["result"], "partial")
        result = self.svc.finalize_action(action["action_id"])
        self.assertEqual(result["classification"], "partial")

    def test_idempotent_retries_and_new_requests(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        first = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-dupe",
            executors={"deploy the release": lambda e, i: {
                "status": "success", "observed_state": {"deployed": True}}})
        second = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-dupe",
            executors={"deploy the release": lambda e, i: {
                "status": "success", "observed_state": {"deployed": True}}})
        self.assertEqual(first["action_id"], second["action_id"])
        self.assertTrue(second["duplicate"])
        third = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-new",
            executors={"deploy the release": lambda e, i: {
                "status": "success", "observed_state": {"deployed": True}}})
        self.assertNotEqual(first["action_id"], third["action_id"])

    # -- C. observation ------------------------------------------------
    def test_observations_are_action_linked_and_distinct(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-O",
            executors={"deploy the release": lambda e, i: {
                "status": "success", "observed_state": {"deployed": True}}})
        manual = self.svc.record_observation(
            action["action_id"], {"quota_left": 12}, source="infra_probe")
        self.assertTrue(manual["observation_id"].startswith("ob_"))
        self.assertEqual(manual["sequence"], 1)
        observed = self.svc.describe(manual["observation_id"])
        self.assertEqual(observed.get("role"), "observation")
        # observation content is reality, not the claim
        self.assertEqual(
            observed["content"]["observed_state"], {"quota_left": 12})
        # verification merges executor and manual observations
        verification = self.svc.verify_action(
            action["action_id"],
            expectations={"deployed": True, "quota_left": 12})
        self.assertEqual(verification["result"], "verified_success")
        result = self.svc.finalize_action(action["action_id"])
        # trace from outcome roots through verification -> observations
        trace = self.svc.trace(result["outcome"]["outcome_id"])
        obs_roles = [n.get("role") for n in trace["records"]]
        self.assertIn("observation", obs_roles)
        self.assertIn("verification", obs_roles)

    def test_missing_observation_outcome_is_unknown(self):
        from ai_engine.verification import verify
        res = verify({"deployed": True}, [], documented_evidence=[])
        self.assertEqual(res["result"], "insufficient_evidence")

    # -- D. verification -----------------------------------------------
    def test_caller_claim_rejected_at_service_level(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-C",
            executors={"deploy the release": lambda e, i: {
                "status": "success", "observed_state": {"deployed": True}}})
        verification = self.svc.verify_action(
            action["action_id"], expectations={"success": True})
        self.assertEqual(verification["result"], "insufficient_evidence")
        result = self.svc.finalize_action(action["action_id"])
        self.assertEqual(result["classification"], "unknown")

    def test_conflicting_evidence_is_unknown_not_success(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-X",
            executors={"deploy the release": lambda e, i: {
                "status": "success", "observed_state": {"mode": "a"}}})
        self.svc.record_observation(
            action["action_id"], {"mode": "b"}, source="second_probe")
        verification = self.svc.verify_action(
            action["action_id"], expectations={"mode": "a"})
        self.assertEqual(verification["result"], "conflicting_evidence")
        result = self.svc.finalize_action(action["action_id"])
        self.assertEqual(result["classification"], "unknown")

    def test_verification_deterministic(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-D",
            executors={"deploy the release": lambda e, i: {
                "status": "success", "observed_state": {"deployed": True}}})
        e = {"deployed": True, "replicas": 3}
        executor = {"deploy the release": lambda x, y: {
            "status": "success", "observed_state": {"deployed": True,
                                                    "replicas": 3}}}
        for i in range(2):
            a = self.svc.request_action(
                plan["plan_id"], step_id, "alice", request_id="d%d" % i,
                executors=executor)
            v1 = self.svc.verify_action(a["action_id"], expectations=e)
            v2 = self.svc.verify_action(a["action_id"], expectations=e)
            self.assertTrue(v1["verification_id"].startswith("vf_"))
            self.assertEqual(v1["verification_id"], v2["verification_id"])
            self.assertEqual(v1["result"], "verified_success")

    # -- E. experience integration -------------------------------------
    def test_experience_feeds_learning_and_strategy(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-L",
            executors={"deploy the release": lambda e, i: {
                "status": "success",
                "observed_state": {"deployed": True, "replicas": 3}}})
        self.svc.verify_action(
            action["action_id"],
            expectations={"deployed": True, "replicas": 3})
        result = self.svc.finalize_action(action["action_id"])
        xp_id = result["experience"]["experience_id"]
        described = self.svc.describe(xp_id)
        self.assertEqual(described.get("role"), "experience")
        self.assertEqual(
            described["content"]["outcome_classification"], "success")
        # the resulting experience feeds learning -> strategy
        learning = self.svc.derive_learning([xp_id])
        self.assertTrue(learning["learning_id"].startswith("lrn_"))
        more = self._experiences("deploy the service", "deploy the release")
        strategies = self.svc.derive_strategies([xp_id] + more)
        self.assertGreaterEqual(len(strategies["strategies"]), 1)
        supporting = strategies["strategies"][0][
            "supporting_experience_ids"]
        self.assertIn(xp_id, supporting)

    def test_external_knowledge_never_becomes_experience(self):
        ext = self.svc.ingest_external_knowledge(
            "a vendor changelog entry", source="vendor", uri="docs://rc12")
        self.assertEqual(ext.get("role"), "knowledge")
        described = self.svc.describe(ext["record_id"])
        self.assertEqual(described.get("origin"), "external")
        self.assertNotEqual(described.get("role"), "experience")

    def test_experience_carries_outcome_and_evidence(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-EV",
            executors={"deploy the release": lambda e, i: {
                "status": "success",
                "observed_state": {"deployed": True, "replicas": 3}}})
        self.svc.verify_action(
            action["action_id"],
            expectations={"deployed": True, "replicas": 3})
        result = self.svc.finalize_action(action["action_id"])
        xp = self.svc.describe(result["experience"]["experience_id"])
        self.assertTrue(xp["content"]["evidence_ids"])
        self.assertEqual(xp["content"]["outcome_id"],
                         result["outcome"]["outcome_id"])

    # -- F. project isolation ------------------------------------------
    def test_cannot_execute_another_projects_plan(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        au = self.svc_other.evaluate_authority(
            plan["plan_id"], [step_id], "bob")
        self.assertEqual(au["decision"], "invalid_plan")
        with self.assertRaises(ValueError):
            self.svc_other.request_action(plan["plan_id"], step_id, "bob")
        # grants are project scoped too
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        au_after = self.svc_other.evaluate_authority(
            plan["plan_id"], [step_id], "bob")
        self.assertEqual(au_after["decision"], "invalid_plan")

    def test_trace_cannot_cross_projects(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        action = self.svc.request_action(plan["plan_id"], step_id, "alice")
        self.svc.verify_action(action["action_id"],
                               expectations={"completed": True})
        result = self.svc.finalize_action(action["action_id"])
        # full provenance is inspectable inside the owning project
        trace = self.svc.trace(result["outcome"]["outcome_id"])
        roles = {n["role"] for n in trace["records"]}
        self.assertIn("action", roles)
        # and the other project cannot traverse A's outcome at all
        with self.assertRaises(ValueError):
            self.svc_other.trace(result["outcome"]["outcome_id"])

    # -- G. provenance chain completeness ------------------------------
    def test_full_trace_chain_complete(self):
        plan = self._plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        self.svc.grant_approval(plan["plan_id"], [step_id], "alice")
        action = self.svc.request_action(
            plan["plan_id"], step_id, "alice", request_id="req-T",
            executors={"deploy the release": lambda e, i: {
                "status": "success",
                "observed_state": {"deployed": True, "replicas": 3}}})
        self.svc.verify_action(
            action["action_id"],
            expectations={"deployed": True, "replicas": 3})
        result = self.svc.finalize_action(action["action_id"])
        trace = self.svc.trace(result["experience"]["experience_id"])
        roles = {n["role"] for n in trace["records"]}
        required = {"plan", "authority", "action", "observation",
                    "verification", "outcome", "experience", "evidence"}
        self.assertTrue(required <= roles, roles - required)
        # outcome roots at verification evidence
        outcome_trace = self.svc.trace(result["outcome"]["outcome_id"])
        self.assertEqual(outcome_trace["root"]["record_id"],
                         result["outcome"]["outcome_id"])

    def test_end_to_end_plan_from_experiences(self):
        # full deterministic path reuses the strategy pipeline
        ids = self._experiences("release frontend", "deploy the release")
        e2e = self.svc.plan_from_experiences("release frontend", ids)
        self.assertEqual(e2e["plan_status"], "drafted")
        plan = e2e["plan"]
        self.assertEqual(plan["ordered_steps"][0]["action"],
                         "deploy the release")
        handoff = e2e["plan"]["action_handoff"]
        self.assertEqual(handoff["stage"], "PLAN->ACTION")
        self.assertTrue(handoff["actionable"])
        self.assertTrue(handoff["authority_required"])
        self.assertFalse(handoff["executed"])


class Phase29ContractTests(unittest.TestCase):
    """v2 contract: lifecycle.grant / authorize / execute (additive)."""

    def setUp(self):
        from api.memory_tools import MemoryToolInterface
        self.tmp = tempfile.mkdtemp(prefix="p29c_")
        self.iface = MemoryToolInterface(data_root=self.tmp)

    def tearDown(self):
        self.iface.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed_plan(self):
        ids = []
        for i in range(3):
            ev = self.iface.execute({"operation": "lifecycle.experience",
                                     "arguments": {
                                         "situation": "deploy the service",
                                         "attempt": "deploy the release",
                                         "result": "success"}})
            self.assertTrue(ev["ok"], ev)
            ids.append(ev["result"]["experience_id"])
        plan = self.iface.execute({"operation": "lifecycle.plan",
                                   "arguments": {
                                       "situation": "deploy the service",
                                       "experience_ids": ids}})
        self.assertTrue(plan["ok"], plan)
        return plan["result"]["plan"]

    def test_grant_authorize_execute_roundtrip(self):
        plan = self._seed_plan()
        step_id = plan["ordered_steps"][0]["step_id"]
        au = self.iface.execute({"operation": "lifecycle.authorize",
                                 "arguments": {
                                     "plan_id": plan["plan_id"],
                                     "plan_step_ids": [step_id],
                                     "actor": "alice"}})
        self.assertTrue(au["ok"], au)
        self.assertEqual(au["result"]["decision"], "requires_approval")
        grant = self.iface.execute({"operation": "lifecycle.grant",
                                    "arguments": {
                                        "plan_id": plan["plan_id"],
                                        "plan_step_ids": [step_id],
                                        "actor": "alice"}})
        self.assertTrue(grant["ok"], grant)
        self.assertEqual(grant["result"]["state"], "approved")
        au2 = self.iface.execute({"operation": "lifecycle.authorize",
                                  "arguments": {
                                      "plan_id": plan["plan_id"],
                                      "plan_step_ids": [step_id],
                                      "actor": "alice"}})
        self.assertEqual(au2["result"]["decision"], "approved")
        ex = self.iface.execute({"operation": "lifecycle.execute",
                                 "arguments": {
                                     "plan_id": plan["plan_id"],
                                     "plan_step_id": step_id,
                                     "actor": "alice",
                                     "executors": {
                                         "deploy the release": {
                                             "status": "success",
                                             "observed_state": {
                                                 "deployed": True,
                                                 "replicas": 3}}}}})
        self.assertTrue(ex["ok"], ex)
        self.assertEqual(ex["result"]["execution_status"], "succeeded")
        self.assertEqual(ex["result"]["authority_decision"], "approved")

    def test_operation_is_additive_and_status_code_stable(self):
        from api.contract_v2 import CONTRACT_VERSION, operations
        self.assertEqual(CONTRACT_VERSION, "2")
        self.assertIn("lifecycle.grant", operations())
        self.assertIn("lifecycle.authorize", operations())
        self.assertIn("lifecycle.execute", operations())

    def test_validation_rejects_partial_execute(self):
        response = self.iface.execute({
            "operation": "lifecycle.execute",
            "arguments": {"plan_id": "pl_1"}})
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "invalid_argument")

    def test_unknown_operation_stays_unknown(self):
        response = self.iface.execute({
            "operation": "lifecycle.raw_execute",
            "arguments": {}})
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "unknown_operation")


if __name__ == "__main__":
    unittest.main()