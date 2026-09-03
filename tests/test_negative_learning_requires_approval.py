"""Phase 8: strategy deprecation requires human approval."""

import unittest

from intelligence.strategy.schema import Strategy
from intelligence.strategy.store import StrategyStore
from intelligence.outcome.schema import Outcome, derive_outcome_id
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification
from intelligence.experience.schema import ExperienceRecord, derive_experience_id
from intelligence.experience.store import ExperienceStore
from intelligence.learning import learn_from_outcome, LearningStore


class NegativeLearningRequiresApprovalTests(unittest.TestCase):
    def test_deprecation_not_auto_applied(self):
        s_store = StrategyStore(":memory:")
        o_store = OutcomeStore(":memory:")
        e_store = ExperienceStore(":memory:")
        l_store = LearningStore(":memory:")
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        strat = Strategy(strategy_id="st_dep", name="dep", description="d",
                         problem_class="bug_fix", tool_sequence=["file.read"],
                         confidence=0.7, strategy_type="explicit",
                         created_at_epoch=1.0, updated_at_epoch=1.0)
        s_store.save(strat)
        for i in range(3):
            oc = Outcome(outcome_id=derive_outcome_id(f"plan{i}", "ctx_a",
                          OutcomeClassification.FAILURE, [f"ev{i}"], {}),
                         plan_id=f"plan{i}", context_id="ctx_a",
                         classification=OutcomeClassification.FAILURE,
                         verification_evidence_ids=(f"ev{i}",), created_at_epoch=float(i))
            o_store.save(oc)
            exp = ExperienceRecord(experience_id=derive_experience_id(f"t{i}", "ctx_a", oc.outcome_id, [f"ev{i}"], "st_dep"),
                                   task_id=f"t{i}", task_type="bug_fix", domain="lint",
                                   context_id="ctx_a", outcome_id=oc.outcome_id,
                                   evidence_ids=(f"ev{i}",), strategy_id="st_dep",
                                   summary={}, synthesized_at_epoch=float(i))
            e_store.save(exp)
        oc_id = derive_outcome_id("plan2", "ctx_a", OutcomeClassification.FAILURE, ["ev2"], {})
        ev = learn_from_outcome(oc_id, context_id="ctx_a", strategy_id="st_dep",
                                outcome_store=o_store, strategy_store=s_store,
                                experience_store=e_store, learning_store=l_store,
                                created_at_epoch=10.0)
        # Deprecation is proposed but not auto-applied; strategy remains not deprecated
        self.assertFalse(s_store.get("st_dep").deprecated)
        dep = [a for a in ev.adaptations_proposed if a.adaptation_type.value == "propose_deprecation"]
        self.assertEqual(len(dep), 1)
        self.assertTrue(dep[0].requires_approval)
        # Deprecation is in proposed but not in applied; it's in rejected as pending approval
        applied_types = {a.adaptation_type.value for a in ev.adaptations_applied}
        self.assertNotIn("propose_deprecation", applied_types)

    def test_human_can_still_deprecate_via_registry(self):
        s_store = StrategyStore(":memory:")
        o_store = OutcomeStore(":memory:")
        e_store = ExperienceStore(":memory:")
        l_store = LearningStore(":memory:")
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        strat = Strategy(strategy_id="st_dep2", name="dep2", description="d",
                         problem_class="bug_fix", tool_sequence=["file.read"],
                         confidence=0.7, strategy_type="explicit",
                         created_at_epoch=1.0, updated_at_epoch=1.0)
        s_store.save(strat)
        for i in range(3):
            oc = Outcome(outcome_id=derive_outcome_id(f"p{i}", "ctx_a",
                          OutcomeClassification.FAILURE, [f"ev{i}"], {}),
                         plan_id=f"p{i}", context_id="ctx_a",
                         classification=OutcomeClassification.FAILURE,
                         verification_evidence_ids=(f"ev{i}",), created_at_epoch=float(i))
            o_store.save(oc)
            exp = ExperienceRecord(experience_id=derive_experience_id(f"t{i}", "ctx_a", oc.outcome_id, [f"ev{i}"], "st_dep2"),
                                   task_id=f"t{i}", task_type="bug_fix", domain="lint",
                                   context_id="ctx_a", outcome_id=oc.outcome_id,
                                   evidence_ids=(f"ev{i}",), strategy_id="st_dep2",
                                   summary={}, synthesized_at_epoch=float(i))
            e_store.save(exp)
        oc_id = derive_outcome_id("p2", "ctx_a", OutcomeClassification.FAILURE, ["ev2"], {})
        learn_from_outcome(oc_id, context_id="ctx_a", strategy_id="st_dep2",
                           outcome_store=o_store, strategy_store=s_store,
                           experience_store=e_store, learning_store=l_store,
                           created_at_epoch=10.0)
        # Human approval via registry explicitly deprecates
        from intelligence.strategy.registry import approve_deprecation
        approve_deprecation("st_dep2", store=s_store, created_at_epoch=11.0)
        self.assertTrue(s_store.get("st_dep2").deprecated)
