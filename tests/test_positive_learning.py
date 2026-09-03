"""Phase 8: successful outcomes increase strategy confidence."""

import unittest

from intelligence.strategy.schema import Strategy
from intelligence.strategy.store import StrategyStore
from intelligence.outcome.schema import Outcome, derive_outcome_id
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification
from intelligence.experience.schema import ExperienceRecord, derive_experience_id
from intelligence.experience.store import ExperienceStore
from intelligence.learning import learn_from_outcome, LearningStore


class PositiveLearningTests(unittest.TestCase):
    def test_success_increases_confidence(self):
        s_store = StrategyStore(":memory:")
        o_store = OutcomeStore(":memory:")
        e_store = ExperienceStore(":memory:")
        l_store = LearningStore(":memory:")
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        strat = Strategy(strategy_id="st_pos", name="pos", description="d",
                         problem_class="bug_fix", tool_sequence=["file.read"],
                         confidence=0.5, strategy_type="explicit",
                         created_at_epoch=1.0, updated_at_epoch=1.0)
        s_store.save(strat)
        # Two successes to meet threshold M>=2
        for i in range(2):
            oc = Outcome(outcome_id=derive_outcome_id(f"plan{i}", "ctx_a",
                          OutcomeClassification.SUCCESS, [f"ev{i}"], {}),
                         plan_id=f"plan{i}", context_id="ctx_a",
                         classification=OutcomeClassification.SUCCESS,
                         verification_evidence_ids=(f"ev{i}",), created_at_epoch=float(i))
            o_store.save(oc)
            exp = ExperienceRecord(experience_id=derive_experience_id(f"t{i}", "ctx_a", oc.outcome_id, [f"ev{i}"], "st_pos"),
                                   task_id=f"t{i}", task_type="bug_fix", domain="lint",
                                   context_id="ctx_a", outcome_id=oc.outcome_id,
                                   evidence_ids=(f"ev{i}",), strategy_id="st_pos",
                                   summary={}, synthesized_at_epoch=float(i))
            e_store.save(exp)
        before = s_store.get("st_pos").confidence
        oc_id = derive_outcome_id("plan1", "ctx_a", OutcomeClassification.SUCCESS, ["ev1"], {})
        ev = learn_from_outcome(oc_id, context_id="ctx_a", strategy_id="st_pos",
                                outcome_store=o_store, strategy_store=s_store,
                                experience_store=e_store, learning_store=l_store,
                                created_at_epoch=10.0)
        after = s_store.get("st_pos").confidence
        self.assertGreater(after, before)
        self.assertEqual(len(ev.adaptations_applied), 1)
        self.assertEqual(ev.adaptations_applied[0].adaptation_type.value,
                         "increase_strategy_confidence")

    def test_single_success_below_threshold_no_increase(self):
        s_store = StrategyStore(":memory:")
        o_store = OutcomeStore(":memory:")
        e_store = ExperienceStore(":memory:")
        l_store = LearningStore(":memory:")
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        strat = Strategy(strategy_id="st_single", name="single", description="d",
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
        exp = ExperienceRecord(experience_id=derive_experience_id("t0", "ctx_a", oc.outcome_id, ["ev0"], "st_single"),
                               task_id="t0", task_type="bug_fix", domain="lint",
                               context_id="ctx_a", outcome_id=oc.outcome_id,
                               evidence_ids=("ev0",), strategy_id="st_single",
                               summary={}, synthesized_at_epoch=0.0)
        e_store.save(exp)
        before = s_store.get("st_single").confidence
        oc_id = oc.outcome_id
        ev = learn_from_outcome(oc_id, context_id="ctx_a", strategy_id="st_single",
                                outcome_store=o_store, strategy_store=s_store,
                                experience_store=e_store, learning_store=l_store,
                                created_at_epoch=10.0)
        after = s_store.get("st_single").confidence
        self.assertEqual(after, before)
        self.assertEqual(len(ev.adaptations_applied), 0)
        self.assertEqual(len(ev.adaptations_rejected), 1)
