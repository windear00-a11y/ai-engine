"""Phase 28 acceptance: the canonical intelligence loop completion.

Covers the deterministic, provenance-preserving, project-isolated path

    Experience -> Learning -> Strategy -> Strategy Application
        -> Reasoning -> Decision -> Plan

through (a) the pure domain engines, (b) the LifecycleService facade and
(c) the v2 contract + MemoryClient. Phase 28 ends at the PLAN boundary: no
action is ever executed and explicit UNKNOWN / insufficient states are never
hidden.
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


def _base():
    return sys.modules["intelligence.lifecycle.model"]._base \
        if hasattr(sys.modules.get("intelligence.lifecycle.model"),
                   "_base") else None


class Phase28EngineModelTests(unittest.TestCase):
    """Pure-domain determinism, status vocabularies and uncertainty surfaces."""

    def _strategy(self, strategy_id="st_1", problem_class="fit things",
                  approach="memory.recall", confidence=0.9,
                  deprecated=False, superseded_by=None,
                  context_restrictions=None, supporting_evidence=()):
        return {
            "strategy_id": strategy_id,
            "name": "strategy " + strategy_id,
            "description": "strategy for fitting things",
            "problem_class": problem_class,
            "tool_sequence": [approach],
            "constraints": {
                "supporting_experience_ids": ["xp_1"],
                "supporting_evidence_ids": list(supporting_evidence),
            },
            "confidence": confidence,
            "context_restrictions": context_restrictions or {},
            "strategy_type": "experience_derived",
            "superseded_by": superseded_by,
            "deprecated": deprecated,
        }

    def _application(self, status="applicable", strategy_id="st_1",
                     candidates=None):
        candidates = candidates or [{
            "strategy_id": strategy_id, "strategy_status": status}]
        return {
            "application_id": "sa_123",
            "status": status,
            "strategy_id": strategy_id if status == "applicable" else None,
            "candidates": candidates,
            "context_id": None,
        }

    # ---- strategy application -------------------------------------------
    def test_application_simple_applicable_deterministic(self):
        from ai_engine.strategy_application import apply_strategies
        s = self._strategy()
        a = apply_strategies("fit things", [s])
        self.assertEqual(a["status"], "applicable")
        self.assertEqual(a["strategy_id"], "st_1")
        self.assertEqual(a["applicable_count"], 1)
        self.assertTrue(a["application_id"].startswith("sa_"))
        b = apply_strategies("fit things", [s])
        self.assertEqual(a, b)

    def test_application_context_restrictions_never_assumed(self):
        from ai_engine.strategy_application import apply_strategies
        restricted = self._strategy(
            context_restrictions={"allowed_contexts": ["ctx_a"]})
        missing = apply_strategies("fit things", [restricted])
        self.assertEqual(missing["status"], "insufficient_evidence")
        self.assertIn("context", missing["uncertainties"][0])
        mismatch = apply_strategies(
            "fit things", [restricted], context_id="ctx_other")
        self.assertEqual(mismatch["status"], "not_applicable")
        inside = apply_strategies("fit things", [restricted],
                                  context_id="ctx_a")
        self.assertEqual(inside["status"], "applicable")

    def test_application_rejects_deprecated_and_superseded(self):
        from ai_engine.strategy_application import apply_strategies
        deprecated = apply_strategies(
            "fit things", [self._strategy(deprecated=True)])
        self.assertEqual(deprecated["status"], "deprecated")
        superseded = apply_strategies(
            "fit things", [self._strategy(superseded_by="st_new")])
        self.assertEqual(superseded["status"], "superseded")

    def test_application_conflict_and_unknown_preserved(self):
        from ai_engine.strategy_application import apply_strategies
        s1 = self._strategy("st_a", approach="approach alpha")
        s2 = self._strategy("st_b", approach="approach beta")
        conflicted = apply_strategies("fit things", [s1, s2])
        self.assertEqual(conflicted["status"], "conflicting")
        self.assertEqual(conflicted["strategy_id"], None)
        self.assertIn("conflict", conflicted["uncertainties"][0])
        unknown = apply_strategies("fit things", [self._strategy(
            problem_class="")])
        self.assertEqual(unknown["status"], "unknown")
        none_ = apply_strategies("fit things", [])
        self.assertEqual(none_["status"], "no_strategy")
        self.assertEqual(none_["candidate_count"], 0)

    # ---- reasoning -------------------------------------------------------
    def test_reasoning_supported_and_provenance(self):
        from ai_engine.reasoning import reason
        s = self._strategy(supporting_evidence=("ev_1",))
        result = reason(
            "fit things", application=self._application(),
            strategies=[s], evidence=[{"evidence_id": "ev_1"}])
        self.assertEqual(result["status"], "supported")
        self.assertEqual(result["applicable_strategy_id"], "st_1")
        self.assertEqual(result["confidence"], 0.9)
        self.assertEqual(result["provenance"]["evidence_ids"], ["ev_1"])
        self.assertTrue(result["reasoning_id"].startswith("rs_"))
        again = reason("fit things", application=self._application(),
                       strategies=[s], evidence=[{"evidence_id": "ev_1"}])
        self.assertEqual(result, again)

    def test_reasoning_missing_evidence_is_uncertainty(self):
        from ai_engine.reasoning import reason
        s = self._strategy(supporting_evidence=("ev_9",))
        result = reason("fit things", application=self._application(),
                        strategies=[s], evidence=[{"evidence_id": "ev_1"}])
        self.assertEqual(result["status"], "insufficient_information")
        self.assertIn("ev_9", result["uncertainties"][0])

    def test_reasoning_conflict_and_empty_unknown(self):
        from ai_engine.reasoning import reason
        s1 = self._strategy("st_a", approach="approach alpha")
        s2 = self._strategy("st_b", approach="approach beta")
        app = self._application(status="conflicting", strategy_id=None,
                                candidates=[
                                    {"strategy_id": "st_a",
                                     "strategy_status": "applicable"},
                                    {"strategy_id": "st_b",
                                     "strategy_status": "applicable"}])
        result = reason("fit things", application=app, strategies=[s1, s2])
        self.assertEqual(result["status"], "conflict")
        self.assertTrue(result["conflicts"])
        empty = reason("fit things")
        self.assertEqual(empty["status"], "insufficient_information")
        orphan = reason("fit things",
                        evidence=[{"evidence_id": "ev_1"}])
        self.assertEqual(orphan["status"], "unknown")
        self.assertIn(
            "uncertainty is preserved", orphan["recommendation"])

    def test_reasoning_bounds_are_bounded(self):
        from ai_engine.reasoning import canonical_bounds
        self.assertEqual(canonical_bounds()["strategy_candidates"], 6)
        tight = canonical_bounds({"strategy_candidates": 1,
                                  "knowledge": 0})
        self.assertEqual(tight["strategy_candidates"], 1)
        self.assertEqual(tight["knowledge"], 12)  # non-positive stays default

    # ---- decision --------------------------------------------------------
    def test_decision_selects_supported_course_deterministically(self):
        from ai_engine.decision import make_decision
        reasoning = {
            "reasoning_id": "rs_1",
            "status": "supported",
            "recommendation": "recall what worked",
            "recommended_approach": "memory.recall",
            "applicable_strategy_id": "st_1",
            "strategy_application_id": "sa_1",
            "context_id": None,
            "confidence": 0.9,
        }
        decision = make_decision(reasoning)
        self.assertEqual(decision["status"], "decided")
        self.assertEqual(decision["selected_course"], "memory.recall")
        self.assertFalse(decision["explicit_unknown"])
        self.assertTrue(decision["decision_id"].startswith("dc_"))
        self.assertEqual(
            decision["provenance"]["reasoning_id"], "rs_1")
        again = make_decision(reasoning)
        self.assertEqual(decision, again)

    def test_decision_constraints_can_eliminate_to_unknown(self):
        from ai_engine.decision import make_decision
        reasoning = {
            "reasoning_id": "rs_1",
            "status": "supported",
            "recommended_approach": "memory.recall",
            "applicable_strategy_id": "st_1",
            "strategy_application_id": "sa_1",
            "context_id": None, "confidence": 0.9,
        }
        eliminated = make_decision(
            reasoning, constraints={"disallowed_approaches": [
                "memory.recall"]})
        self.assertEqual(eliminated["status"], "insufficient_evidence")
        self.assertTrue(eliminated["explicit_unknown"])
        self.assertIsNone(eliminated["selected_option_id"])

    def test_decision_ambiguity_and_unknown_are_explicit(self):
        from ai_engine.decision import make_decision
        reasoning = {
            "reasoning_id": "rs_2",
            "status": "supported",
            "recommended_approach": "memory.recall",
            "applicable_strategy_id": "st_1",
            "strategy_application_id": "sa_2",
            "context_id": None, "confidence": 0.6,
        }
        ambiguous = make_decision(
            reasoning, alternatives=[{"course": "alpha"},
                                     {"course": "beta"}])
        self.assertEqual(ambiguous["status"], "ambiguous")
        self.assertEqual(len(ambiguous["alternatives"]), 3)
        unknown = make_decision({"reasoning_id": "rs_3", "status": "unknown"})
        self.assertEqual(unknown["status"], "unknown")
        self.assertTrue(unknown["explicit_unknown"])

    # ---- plan ------------------------------------------------------------
    def _decided_decision(self, course="memory.recall", reasoning_id="rs_1"):
        return {
            "decision_id": "dc_1",
            "status": "decided",
            "selected_course": course,
            "reasoning_id": reasoning_id,
            "strategy_application_id": "sa_1",
            "applicable_strategy_id": "st_1",
            "constraints": {},
            "context_id": None,
            "situation": "fit things",
        }

    def test_plan_drafted_with_stable_step_id_and_handoff(self):
        from ai_engine.plan import build_plan
        plan = build_plan(self._decided_decision(), "fit things")
        self.assertEqual(plan["status"], "drafted")
        self.assertTrue(plan["plan_id"].startswith("pl_"))
        self.assertEqual(len(plan["ordered_steps"]), 1)
        step = plan["ordered_steps"][0]
        self.assertTrue(step["step_id"].startswith("pls_"))
        self.assertTrue(step["preconditions"])
        self.assertIn("OBSERVATION", step["expected_observation"])
        self.assertIn("VERIFICATION", step["verification_requirement"])
        self.assertEqual(plan["provenance"]["decision_id"], "dc_1")
        handoff = plan["action_handoff"]
        self.assertEqual(handoff["stage"], "PLAN->ACTION")
        self.assertIs(handoff["ready"], False)
        self.assertIs(handoff["approval_pending"], True)
        self.assertIs(handoff["executed"], False)
        self.assertIn("ACTION", handoff["phase_29_contracts"])
        again = build_plan(self._decided_decision(), "fit things")
        self.assertEqual(plan, again)
        self.assertEqual(step["step_id"],
                         plan["ordered_steps"][0]["step_id"])

    def test_plan_unactionable_when_not_decided(self):
        from ai_engine.plan import build_plan
        plan = build_plan({"decision_id": "dc_2",
                           "status": "insufficient_evidence",
                           "situation": "fit things"}, "fit things")
        self.assertEqual(plan["status"], "unactionable")
        self.assertEqual(plan["ordered_steps"], [])
        self.assertIs(plan["action_handoff"]["actionable"], False)

    # ---- action boundary is contract-only -------------------------------
    def test_action_boundary_is_contract_only(self):
        from ai_engine.action_boundary import (
            BOUNDARY_STAGES, PHASE_29_IMPLEMENTED, ActionRequest,
            ActionStep)
        self.assertIs(PHASE_29_IMPLEMENTED, False)
        self.assertEqual(BOUNDARY_STAGES,
                         ("ACTION", "OBSERVATION", "VERIFICATION", "OUTCOME"))
        step = ActionStep(plan_id="pl_1", step_id="pls_1", action="noop")
        request = ActionRequest(plan_id="pl_1", step=step)
        self.assertIsNone(request.approval_token)

    # ---- the new engines are pure (model/extend, not storage) -----------
    def test_new_engines_are_pure(self):
        code = (
            "from ai_engine.strategy_application import apply_strategies, "
            "derive_application_id\n"
            "from ai_engine.reasoning import reason, derive_reasoning_id\n"
            "from ai_engine.decision import make_decision, derive_decision_id\n"
            "from ai_engine.plan import build_plan, derive_plan_step_id\n"
            "from ai_engine.action_boundary import PHASE_29_IMPLEMENTED\n"
            "assert PHASE_29_IMPLEMENTED is False\n"
            "print('PURE_OK')\n"
        )
        r = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("PURE_OK", r.stdout)
        for file_name in ("strategy_application.py", "reasoning.py",
                          "decision.py", "plan.py", "action_boundary.py"):
            text = os.path.join(_ROOT, "ai_engine", file_name)
            with open(text) as f:
                source = f.read()
            for token in ("sqlite", "requests", "openai", "anthropic",
                          "urllib", "socket", "subprocess"):
                self.assertNotIn(token, source, file_name + " leaks " + token)


class Phase28IntelligenceLoopServiceTests(unittest.TestCase):
    """Acceptance A-G driven through ai_engine.lifecycle_service."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p28_")
        from ai_engine.lifecycle_service import LifecycleService
        self.svc = LifecycleService(project_id="pl28", data_root=self.tmp)
        self._exp_counter = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _experiences(self, situation, attempt, outcomes, task_type="generic"):
        ids = []
        for outcome in outcomes:
            self._exp_counter += 1
            ev = self.svc.record_evidence(
                "obs_%d" % self._exp_counter,
                "verified outcome %d" % self._exp_counter)
            xp = self.svc.record_experience(
                situation, attempt, outcome, evidence_ids=[ev["evidence_id"]],
                task_type=task_type)
            ids.append(xp["experience_id"])
            self._ctx = xp["context_id"]
        return ids

    def _strategies(self, situation, attempts):
        """Derive one strategy per attempt (>=3 samples each) and return
        (strategies, context_id)."""
        ids = []
        for attempt in attempts:
            ids.extend(self._experiences(situation, attempt,
                                         ["success", "success", "success"]))
        result = self.svc.derive_strategies(
            ids, project_id="pl28")["strategies"]
        return result, self._ctx

    # ---- A: application statuses through the service ---------------------
    def test_A_application_statuses_via_service(self):
        situation = "tune the database"
        ids = self._experiences(situation, "raise cache", ["success"] * 3)
        ctx = self._ctx
        strategies = self.svc.derive_strategies(ids)["strategies"]
        strategy_ids = [s["strategy_id"] for s in strategies]
        app = self.svc.apply_strategy(situation, strategy_ids=strategy_ids,
                                     context_id=ctx)
        self.assertEqual(app["status"], "applicable")
        self.assertEqual(app["role"], "strategy_application")
        self.assertEqual(app["origin"], "derived")
        self.assertTrue(app["application_id"].startswith("sa_"))
        self.assertEqual(app["provenance"]["strategy_ids"], strategy_ids)
        # determinism: identical inputs -> identical application id
        again = self.svc.apply_strategy(situation, strategy_ids=strategy_ids,
                                        context_id=ctx)
        self.assertEqual(again["application_id"], app["application_id"])
        # deprecated via retract: a deprecated strategy is never applicable
        retracted = self.svc.retract(strategy_ids[0], reason="phased out")
        self.assertEqual(retracted["lifecycle_state"], "invalidated")
        dep = self.svc.apply_strategy(situation,
                                      strategy_ids=[strategy_ids[0]],
                                      context_id=ctx)
        self.assertEqual(dep["status"], "deprecated")
        # no candidates at all -> no_strategy
        none_ = self.svc.apply_strategy(situation, strategy_ids=[])
        self.assertEqual(none_["status"], "no_strategy")

    def test_A_superseded_strategy_is_rejected(self):
        strategies, ctx = self._strategies("tune the database",
                                           ["raise cache", "rewrite query"])
        self.assertEqual(len(strategies), 2)
        old_id = strategies[0]["strategy_id"]
        new_id = strategies[1]["strategy_id"]
        self.svc.supersede(old_id, new_id, reason="replaced approach")
        app = self.svc.apply_strategy("tune the database",
                                      strategy_ids=[old_id],
                                      context_id=ctx)
        self.assertEqual(app["status"], "superseded")

    def test_A_conflicting_strategies_not_arbitrarily_selected(self):
        strategies, ctx = self._strategies("tune the database",
                                           ["raise cache", "rewrite query"])
        self.assertEqual(len(strategies), 2)
        app = self.svc.apply_strategy("tune the database",
                                      strategy_ids=[s["strategy_id"]
                                                    for s in strategies],
                                      context_id=ctx)
        self.assertEqual(app["status"], "conflicting")
        self.assertIsNone(app["strategy_id"])
        self.assertEqual(app["applicable_count"], 2)

    # ---- B: reasoning structured inputs and uncertainty ------------------
    def test_B_reasoning_supported_with_structured_inputs(self):
        situation = "release the service"
        ids = self._experiences(situation, "run release pipeline",
                                ["success"] * 3)
        ctx = self._ctx
        strategies = self.svc.derive_strategies(ids)["strategies"]
        strategy_ids = [s["strategy_id"] for s in strategies]
        app = self.svc.apply_strategy(situation, strategy_ids=strategy_ids,
                                     context_id=ctx)
        result = self.svc.reason(
            situation,
            strategy_application_id=app["application_id"],
            context_id=ctx,
            experience_ids=ids,
            evidence_ids=app["provenance"]["supporting_evidence_ids"])
        self.assertEqual(result["status"], "supported")
        self.assertTrue(result["reasoning_id"].startswith("rs_"))
        self.assertEqual(result["role"], "reasoning")
        self.assertEqual(result["origin"], "derived")
        self.assertIn("ev_", result["provenance"]["evidence_ids"][0])
        self.assertEqual(result["provenance"]["strategy_application_id"],
                         app["application_id"])
        self.assertEqual(sorted(ids),
                         result["inputs"]["experience_ids"])

    def test_B_reasoning_reports_insufficient_information(self):
        result = self.svc.reason("some unknown situation")
        self.assertEqual(result["status"], "insufficient_information")
        self.assertEqual(result["inputs"]["strategy_ids"], [])

    def test_B_reasoning_surfaces_strategy_conflict(self):
        strategies, ctx = self._strategies("tune the database",
                                           ["raise cache", "rewrite query"])
        app = self.svc.apply_strategy(
            "tune the database",
            strategy_ids=[s["strategy_id"] for s in strategies],
            context_id=ctx)
        result = self.svc.reason(
            "tune the database",
            strategy_application_id=app["application_id"])
        self.assertEqual(result["status"], "conflict")
        self.assertTrue(result["conflicts"])

    # ---- C: decision distinguishes reasoning from selection ---------------
    def test_C_decision_selects_and_respects_constraints(self):
        situation = "purge build cache"
        ids = self._experiences(situation, "rm -rf build", ["success"] * 3)
        ctx = self._ctx
        strategies = self.svc.derive_strategies(ids)["strategies"]
        strategy_ids = [s["strategy_id"] for s in strategies]
        app = self.svc.apply_strategy(situation, strategy_ids=strategy_ids,
                                     context_id=ctx)
        reasoning = self.svc.reason(
            situation, strategy_application_id=app["application_id"],
            evidence_ids=app["provenance"]["supporting_evidence_ids"])
        decision = self.svc.decide(reasoning=reasoning)
        self.assertEqual(decision["status"], "decided")
        self.assertTrue(decision["decision_id"].startswith("dc_"))
        self.assertEqual(decision["reasoning_id"], reasoning["reasoning_id"])
        self.assertFalse(decision["explicit_unknown"])
        # eliminating the selected course must surface as insufficient
        blocked = self.svc.decide(
            reasoning=reasoning,
            constraints={"disallowed_approaches":
                         [decision["selected_course"]]})
        self.assertIn(blocked["status"],
                      ("insufficient_evidence", "unknown"))
        self.assertTrue(blocked["explicit_unknown"])
        self.assertIsNone(blocked["selected_course"])
        # determinism of the decision identity
        decision_again = self.svc.decide(reasoning=reasoning)
        self.assertEqual(decision_again["decision_id"],
                         decision["decision_id"])

    # ---- D: plan is an intended sequence, never execution -----------------
    def test_D_plan_contract_and_stable_identity(self):
        situation = "release the package"
        ids = self._experiences(situation, "run release pipeline",
                                ["success"] * 3)
        e2e = self.svc.plan_from_experiences(situation, ids)
        plan = e2e["plan"]
        self.assertEqual(plan["status"], "drafted")
        self.assertTrue(plan["plan_id"].startswith("pl_"))
        self.assertEqual(plan["decision_id"], e2e["decision_id"])
        step = plan["ordered_steps"][0]
        self.assertTrue(step["step_id"].startswith("pls_"))
        self.assertEqual(plan["provenance"]["reasoning_id"],
                         e2e["reasoning_id"])
        self.assertTrue(step["preconditions"])
        self.assertTrue(plan["preconditions"])
        self.assertTrue(plan["expected_observations"])
        self.assertTrue(plan["verification_requirements"])
        self.assertIs(plan["action_handoff"]["ready"], False)
        self.assertIs(plan["action_handoff"]["executed"], False)
        # a plan record is inspectable with full provenance
        described = self.svc.describe(plan["plan_id"])
        self.assertEqual(described["role"], "plan")
        self.assertEqual(described["origin"], "derived")
        self.assertIn("action_handoff", described["content"])
        # re-running the full path yields the same ids (idempotent)
        again = self.svc.plan_from_experiences(situation, ids)
        self.assertEqual(again["plan_id"], plan["plan_id"])
        self.assertEqual(again["decision_id"], e2e["decision_id"])
        self.assertEqual(again["reasoning_id"], e2e["reasoning_id"])

    def test_D_uncertainty_path_ends_at_unactionable_plan(self):
        situation = "migrate schema"
        ids = self._experiences(situation, "apply migration",
                                ["success"] * 3)
        e2e = self.svc.plan_from_experiences(
            situation, ids, context_id=None,
            constraints={"disallowed_approaches": ["apply migration"]})
        self.assertEqual(e2e["decision_status"], "insufficient_evidence")
        self.assertEqual(e2e["plan_status"], "unactionable")
        self.assertEqual(e2e["plan"]["ordered_steps"], [])
        self.assertIs(e2e["plan"]["action_handoff"]["actionable"], False)
        self.assertIn("no action was executed", e2e["note"])

    # ---- E: project isolation ---------------------------------------------
    def test_E_projects_are_isolated(self):
        from ai_engine.lifecycle_service import LifecycleService
        else_svc = LifecycleService(project_id="pl28b", data_root=self.tmp)
        situation = "restart the worker"
        ids = self._experiences(situation, "systemctl restart",
                                ["success"] * 3)
        ctx = self._ctx
        strategies = self.svc.derive_strategies(ids)["strategies"]
        app = self.svc.apply_strategy(
            situation, strategy_ids=[s["strategy_id"]
                                     for s in strategies],
            context_id=ctx)
        self.assertEqual(app["status"], "applicable")
        # the other project has none of this project's strategies
        else_summary = else_svc.summary()
        self.assertEqual(else_summary["by_role"].get("strategy", 0), 0)
        self.assertEqual(else_summary["by_role"].get("experience", 0), 0)
        isolated = else_svc.apply_strategy(situation, strategy_ids=[])
        self.assertEqual(isolated["status"], "no_strategy")
        self.assertEqual(isolated["candidate_count"], 0)
        # and it cannot find this project's strategy by explicit id
        other = else_svc.apply_strategy(
            situation, strategy_ids=[strategies[0]["strategy_id"]])
        self.assertEqual(other["candidate_count"], 0)

    # ---- F: end-to-end canonical path with complete provenance ------------
    def test_F_full_path_ids_and_provenance_chain(self):
        situation = "deploy the service"
        ids = self._experiences(situation, "canary then full",
                                ["success"] * 3, task_type="deploy")
        e2e = self.svc.plan_from_experiences(situation, ids)
        self.assertEqual(e2e["chain"],
                         ["experience", "learning", "strategy",
                          "strategy_application", "reasoning", "decision",
                          "plan"])
        self.assertTrue(e2e["learning_id"].startswith("lrn_"))
        for s in e2e["strategy_ids"]:
            self.assertTrue(s.startswith("st_"))
        self.assertEqual(e2e["strategy_application_status"], "applicable")
        self.assertEqual(e2e["reasoning_status"], "supported")
        self.assertEqual(e2e["decision_status"], "decided")
        self.assertEqual(e2e["plan_status"], "drafted")
        self.assertTrue(e2e["strategy_application_id"].startswith("sa_"))
        self.assertTrue(e2e["reasoning_id"].startswith("rs_"))
        self.assertTrue(e2e["decision_id"].startswith("dc_"))
        self.assertTrue(e2e["plan_id"].startswith("pl_"))
        # the plan trace walks the whole chain back to observed origins
        trace = self.svc.trace(e2e["plan_id"])
        roles = {n["role"] for n in trace["records"]}
        self.assertEqual(
            roles,
            {"plan", "decision", "reasoning", "strategy_application",
             "strategy", "learning", "experience", "outcome", "evidence",
             "context"})
        # every derived record is persisted with the derived origin
        for record_id in (e2e["strategy_application_id"],
                          e2e["reasoning_id"], e2e["decision_id"],
                          e2e["plan_id"]):
            described = self.svc.describe(record_id)
            self.assertEqual(described["origin"], "derived")
            self.assertEqual(described["lifecycle_state"], "active")
        # the summary reflects the phase-28 roles
        summary = self.svc.summary()
        for role in ("strategy_application", "reasoning", "decision", "plan"):
            self.assertGreaterEqual(summary["by_role"].get(role, 0), 1)

    # ---- G: cross-data-root determinism ------------------------------------
    def test_G_identical_inputs_over_different_stores(self):
        from ai_engine.lifecycle_service import LifecycleService
        other_tmp = tempfile.mkdtemp(prefix="p28b_")
        other = LifecycleService(project_id="pl28", data_root=other_tmp)
        try:
            situation = "backfill the index"
            mine = self.svc.plan_from_experiences(
                situation,
                self._experiences(situation, "run backfill",
                                  ["success"] * 3))
            theirs = other.plan_from_experiences(
                situation,
                self._others(other, situation, "run backfill",
                             ["success"] * 3))
            for key in ("learning_id", "strategy_ids",
                        "strategy_application_id",
                        "strategy_application_status", "reasoning_id",
                        "reasoning_status", "decision_id", "decision_status",
                        "plan_id", "plan_status"):
                self.assertEqual(mine[key], theirs[key], key)
        finally:
            shutil.rmtree(other_tmp, ignore_errors=True)

    def _others(self, svc, situation, attempt, outcomes):
        ids = []
        for i, outcome in enumerate(outcomes, 1):
            ev = svc.record_evidence(
                "obs_%d" % i, "verified outcome %d" % i)
            xp = svc.record_experience(situation, attempt, outcome,
                                       evidence_ids=[ev["evidence_id"]])
            ids.append(xp["experience_id"])
        return ids


class Phase28IntelligenceLoopAPIIntegration(unittest.TestCase):
    """Phase 28 through the v2 contract and the MemoryClient SDK."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="p28api_")
        from api.memory_tools import MemoryToolInterface
        self.iface = MemoryToolInterface(data_root=self.tmp)
        self._exp_counter = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _call(self, operation, arguments):
        res = self.iface.execute({"operation": operation,
                                  "arguments": arguments})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["contract_version"], "2")
        return res["result"]

    def _experiences(self, situation, attempt):
        ids = []
        for _ in range(3):
            self._exp_counter += 1
            xp = self._call("lifecycle.experience", {
                "situation": situation, "attempt": attempt,
                "result": "success",
                "evidence_ids": [],
                "task_type": "generic"})
            ids.append(xp["experience_id"])
        return ids

    def test_e2e_full_loop_via_v2_contract(self):
        situation = "restore from backup"
        ids = self._experiences(situation, "restore latest")
        result = self._call("lifecycle.plan", {
            "situation": situation,
            "experience_ids": ids})
        self.assertEqual(result["strategy_application_status"], "applicable")
        self.assertEqual(result["reasoning_status"], "supported")
        self.assertEqual(result["decision_status"], "decided")
        self.assertEqual(result["plan_status"], "drafted")
        # the plan is reachable through the existing inspect/trace path
        trace = self._call("lifecycle.trace",
                           {"record_id": result["plan_id"]})
        roles = {n["role"] for n in trace["records"]}
        self.assertIn("decision", roles)
        self.assertIn("reasoning", roles)
        self.assertIn("strategy_application", roles)
        # errors surface as invalid_argument (empty situation)
        res = self.iface.execute({
            "operation": "lifecycle.plan",
            "arguments": {"situation": "", "experience_ids": ids}})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "invalid_argument")

    def test_memory_client_plan_method(self):
        from knowledge_client.memory_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        client = MemoryClient(transport=MemoryInProcessTransport(
            data_root=self.tmp))
        situation = "rotate the certificate"
        ids = self._experiences(situation, "rotate cert")
        plan = client.plan(situation, ids)
        self.assertTrue(plan["plan_id"].startswith("pl_"))
        self.assertEqual(plan["plan_status"], "drafted")
        self.assertEqual(plan["decision_status"], "decided")
        # SDK validation rejects empty situation
        from knowledge_client.errors import InvalidArgumentError
        with self.assertRaises(InvalidArgumentError):
            client.plan("", ids)

    def test_cli_lifecycle_plan(self):
        driver = os.path.join(self.tmp, "driver_cli_p28.py")
        template = (
            "import os, json, subprocess, sys\n"
            "sys.path.insert(0, __ROOT__)\n"
            "from ai_engine.lifecycle_service import LifecycleService\n"
            "data_dir = __TMP__\n"
            "svc = LifecycleService(project_id='cli28', data_root=data_dir)\n"
            "ids = []\n"
            "for i in range(3):\n"
            "    ev = svc.record_evidence('cli_obs_%d' % i, 'c %d' % i)\n"
            "    xp = svc.record_experience('run the job', 'run job once', "
            "'success', evidence_ids=[ev['evidence_id']])\n"
            "    ids.append(xp['experience_id'])\n"
            "env = dict(os.environ)\n"
            "env['AI_ENGINE_DATA_DIR'] = data_dir\n"
            "cmd = [sys.executable, '-m', 'ai_engine', 'lifecycle', "
            "'plan', 'run the job', ','.join(ids), '--project', "
            "'cli28', '--json']\n"
            "r = subprocess.run(cmd, capture_output=True, text=True, "
            "env=env, cwd=__ROOT__)\n"
            "sys.stderr.write(r.stderr)\n"
            "if r.returncode != 0:\n"
            "    sys.exit(r.returncode)\n"
            "result = json.loads(r.stdout)\n"
            "assert result['plan_id'].startswith('pl_'), result\n"
            "assert result['plan_status'] == 'drafted', result\n"
            "assert len(result['plan']['ordered_steps']) == 1\n"
            "print('CLI_PLAN_OK')\n"
        ).replace("__TMP__", json.dumps(self.tmp))
        template = template.replace("__ROOT__", json.dumps(_ROOT))
        with open(driver, "w") as f:
            f.write(template)
        r = subprocess.run([sys.executable, driver],
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CLI_PLAN_OK", r.stdout)

    def test_v1_contract_stays_frozen(self):
        from api.contract import CONTRACT_VERSION, OPERATIONS
        self.assertEqual(CONTRACT_VERSION, "1")
        self.assertEqual(tuple(OPERATIONS),
                         ("search", "get", "related", "follow", "provenance",
                          "inspect"))
        for op in OPERATIONS:
            self.assertNotIn("lifecycle.", op)
        from api.contract_v2 import operations as v2_ops
        self.assertIn("lifecycle.plan", v2_ops())


if __name__ == "__main__":
    unittest.main()