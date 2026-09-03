"""Phase 8: a deprecated strategy can be restored by new success evidence."""

import unittest

from intelligence.strategy.schema import Strategy
from intelligence.strategy.store import StrategyStore
from intelligence.outcome.schema import Outcome, derive_outcome_id
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification
from intelligence.experience.schema import ExperienceRecord, derive_experience_id
from intelligence.experience.store import ExperienceStore
from intelligence.learning import learn_from_outcome, LearningStore


class LearningReversibilityTests(unittest.TestCase):
    def test_restore_after_new_success(self):
        s_store = StrategyStore(":memory:")
        o_store = OutcomeStore(":memory:")
        e_store = ExperienceStore(":memory:")
        l_store = LearningStore(":memory:")
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        strat = Strategy(strategy_id="st_rev", name="rev", description="d",
                         problem_class="bug_fix", tool_sequence=["file.read"],
                         confidence=0.7, strategy_type="explicit",
                         created_at_epoch=1.0, updated_at_epoch=1.0)
        s_store.save(strat)
        # Deprecate via registry (human approval)
        s_store.set_deprecated("st_rev", True, 5.0, note="deprecated for test")
        self.assertTrue(s_store.get("st_rev").deprecated)
        # New success outcome
        oc = Outcome(outcome_id=derive_outcome_id("plan_ok", "ctx_a",
                      OutcomeClassification.SUCCESS, ["ev_ok"], {}),
                     plan_id="plan_ok", context_id="ctx_a",
                     classification=OutcomeClassification.SUCCESS,
                     verification_evidence_ids=("ev_ok",), created_at_epoch=6.0)
        o_store.save(oc)
        exp = ExperienceRecord(experience_id=derive_experience_id("t_ok", "ctx_a", oc.outcome_id, ["ev_ok"], "st_rev"),
                               task_id="t_ok", task_type="bug_fix", domain="lint",
                               context_id="ctx_a", outcome_id=oc.outcome_id,
                               evidence_ids=("ev_ok",), strategy_id="st_rev",
                               summary={}, synthesized_at_epoch=6.0)
        e_store.save(exp)
        ev = learn_from_outcome(oc.outcome_id, context_id="ctx_a", strategy_id="st_rev",
                                outcome_store=o_store, strategy_store=s_store,
                                experience_store=e_store, learning_store=l_store,
                                created_at_epoch=10.0)
        # Restoration applied
        applied_types = {a.adaptation_type.value for a in ev.adaptations_applied}
        self.assertIn("restore_strategy", applied_types)
        self.assertFalse(s_store.get("st_rev").deprecated)

    def test_non_deprecated_success_does_not_restore(self):
        s_store = StrategyStore(":memory:")
        o_store = OutcomeStore(":memory:")
        e_store = ExperienceStore(":memory:")
        l_store = LearningStore(":memory:")
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        strat = Strategy(strategy_id="st_norev", name="norev", description="d",
                         problem_class="bug_fix", tool_sequence=["file.read"],
                         confidence=0.5, strategy_type="explicit",
                         created_at_epoch=1.0, updated_at_epoch=1.0)
        s_store.save(strat)
        for i in range(2):
            oc = Outcome(outcome_id=derive_outcome_id(f"plan{i}", "ctx_a",
                          OutcomeClassification.SUCCESS, [f"ev{i}"], {}),
                         plan_id=f"plan{i}", context_id="ctx_a",
                         classification=OutcomeClassification.SUCCESS,
                         verification_evidence_ids=(f"ev{i}",), created_at_epoch=float(i))
            o_store.save(oc)
            exp = ExperienceRecord(experience_id=derive_experience_id(f"t{i}", "ctx_a", oc.outcome_id, [f"ev{i}"], "st_norev"),
                                   task_id=f"t{i}", task_type="bug_fix", domain="lint",
                                   context_id="ctx_a", outcome_id=oc.outcome_id,
                                   evidence_ids=(f"ev{i}",), strategy_id="st_norev",
                                   summary={}, synthesized_at_epoch=float(i))
            e_store.save(exp)
        oc_id = derive_outcome_id("plan1", "ctx_a", OutcomeClassification.SUCCESS, ["ev1"], {})
        ev = learn_from_outcome(oc_id, context_id="ctx_a", strategy_id="st_norev",
                                outcome_store=o_store, strategy_store=s_store,
                                experience_store=e_store, learning_store=l_store,
                                created_at_epoch=10.0)
        applied_types = {a.adaptation_type.value for a in ev.adaptations_applied}
        self.assertIn("increase_strategy_confidence", applied_types)
        self.assertNotIn("restore_strategy", applied_types)
