"""Phase 8: adaptations below threshold are rejected."""

import unittest

from intelligence.learning.validation import validate_adaptation, MIN_SUCCESS_FOR_INCREASE, MIN_FAILURE_FOR_DECREASE
from intelligence.learning.types import Adaptation, AdaptationType
from intelligence.learning.schema import derive_adaptation_id


class LearningPolicyThresholdTests(unittest.TestCase):
    def test_below_threshold_rejected(self):
        adapt = Adaptation(adaptation_id=derive_adaptation_id(AdaptationType.INCREASE_STRATEGY_CONFIDENCE, "st_x", "ctx_a", ["ev1"]),
                           adaptation_type=AdaptationType.INCREASE_STRATEGY_CONFIDENCE,
                           target_id="st_x", delta=0.05, context_id="ctx_a",
                           evidence_ids=["ev1"])
        v = validate_adaptation(adapt, evidence_count=1)
        self.assertFalse(v.approved)
        self.assertIn(str(MIN_SUCCESS_FOR_INCREASE), v.reason)

    def test_at_threshold_approved(self):
        adapt = Adaptation(adaptation_id=derive_adaptation_id(AdaptationType.INCREASE_STRATEGY_CONFIDENCE, "st_x", "ctx_a", ["ev1"]),
                           adaptation_type=AdaptationType.INCREASE_STRATEGY_CONFIDENCE,
                           target_id="st_x", delta=0.05, context_id="ctx_a",
                           evidence_ids=["ev1"])
        v = validate_adaptation(adapt, evidence_count=MIN_SUCCESS_FOR_INCREASE)
        self.assertTrue(v.approved)

    def test_negative_below_threshold_rejected(self):
        adapt = Adaptation(adaptation_id=derive_adaptation_id(AdaptationType.DECREASE_STRATEGY_CONFIDENCE, "st_x", "ctx_a", ["ev1"]),
                           adaptation_type=AdaptationType.DECREASE_STRATEGY_CONFIDENCE,
                           target_id="st_x", delta=-0.05, context_id="ctx_a",
                           evidence_ids=["ev1"])
        v = validate_adaptation(adapt, evidence_count=2)
        self.assertFalse(v.approved)
        self.assertIn(str(MIN_FAILURE_FOR_DECREASE), v.reason)

    def test_negative_at_threshold_approved(self):
        adapt = Adaptation(adaptation_id=derive_adaptation_id(AdaptationType.DECREASE_STRATEGY_CONFIDENCE, "st_x", "ctx_a", ["ev1"]),
                           adaptation_type=AdaptationType.DECREASE_STRATEGY_CONFIDENCE,
                           target_id="st_x", delta=-0.05, context_id="ctx_a",
                           evidence_ids=["ev1"])
        v = validate_adaptation(adapt, evidence_count=MIN_FAILURE_FOR_DECREASE)
        self.assertTrue(v.approved)

    def test_learning_engine_rejects_below_threshold(self):
        # End-to-end: single success is below threshold, so learning rejects.
        from intelligence.strategy.schema import Strategy
        from intelligence.strategy.store import StrategyStore
        from intelligence.outcome.schema import Outcome, derive_outcome_id
        from intelligence.outcome.store import OutcomeStore
        from intelligence.outcome.types import OutcomeClassification
        from intelligence.experience.schema import ExperienceRecord, derive_experience_id
        from intelligence.experience.store import ExperienceStore
        from intelligence.learning import learn_from_outcome, LearningStore
        s_store = StrategyStore(":memory:")
        o_store = OutcomeStore(":memory:")
        e_store = ExperienceStore(":memory:")
        l_store = LearningStore(":memory:")
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        strat = Strategy(strategy_id="st_thr", name="thr", description="d",
                         problem_class="bug_fix", tool_sequence=["file.read"],
                         confidence=0.5, strategy_type="explicit",
                         created_at_epoch=1.0, updated_at_epoch=1.0)
        s_store.save(strat)
        oc = Outcome(outcome_id=derive_outcome_id("plan0", "ctx_a",
                      OutcomeClassification.SUCCESS, ["ev0"], {}),
                     plan_id="plan0", context_id="ctx_a",
                     classification=OutcomeClassification.SUCCESS,
                     verification_evidence_ids=("ev0",), created_at_epoch=0.0)
        o_store.save(oc)
        exp = ExperienceRecord(experience_id=derive_experience_id("t0", "ctx_a", oc.outcome_id, ["ev0"], "st_thr"),
                               task_id="t0", task_type="bug_fix", domain="lint",
                               context_id="ctx_a", outcome_id=oc.outcome_id,
                               evidence_ids=("ev0",), strategy_id="st_thr",
                               summary={}, synthesized_at_epoch=0.0)
        e_store.save(exp)
        ev = learn_from_outcome(oc.outcome_id, context_id="ctx_a", strategy_id="st_thr",
                                outcome_store=o_store, strategy_store=s_store,
                                experience_store=e_store, learning_store=l_store,
                                created_at_epoch=10.0)
        self.assertEqual(len(ev.adaptations_applied), 0)
        self.assertEqual(len(ev.adaptations_rejected), 1)
