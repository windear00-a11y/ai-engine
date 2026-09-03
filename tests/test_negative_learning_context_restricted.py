"""Phase 8: negative findings are context-restricted."""

import unittest

from intelligence.strategy.schema import Strategy
from intelligence.strategy.store import StrategyStore
from intelligence.outcome.schema import Outcome, derive_outcome_id
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification
from intelligence.experience.schema import ExperienceRecord, derive_experience_id
from intelligence.experience.store import ExperienceStore
from intelligence.learning import learn_from_outcome, LearningStore


class NegativeLearningContextRestrictedTests(unittest.TestCase):
    def test_negative_adaptations_have_context_restriction(self):
        s_store = StrategyStore(":memory:")
        o_store = OutcomeStore(":memory:")
        e_store = ExperienceStore(":memory:")
        l_store = LearningStore(":memory:")
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        strat = Strategy(strategy_id="st_ctx", name="ctx", description="d",
                         problem_class="bug_fix", tool_sequence=["file.read"],
                         confidence=0.7, strategy_type="explicit",
                         created_at_epoch=1.0, updated_at_epoch=1.0)
        s_store.save(strat)
        for i in range(3):
            oc = Outcome(outcome_id=derive_outcome_id(f"plan{i}", "ctx_x",
                          OutcomeClassification.FAILURE, [f"ev{i}"], {}),
                         plan_id=f"plan{i}", context_id="ctx_x",
                         classification=OutcomeClassification.FAILURE,
                         verification_evidence_ids=(f"ev{i}",), created_at_epoch=float(i))
            o_store.save(oc)
            exp = ExperienceRecord(experience_id=derive_experience_id(f"t{i}", "ctx_x", oc.outcome_id, [f"ev{i}"], "st_ctx"),
                                   task_id=f"t{i}", task_type="bug_fix", domain="lint",
                                   context_id="ctx_x", outcome_id=oc.outcome_id,
                                   evidence_ids=(f"ev{i}",), strategy_id="st_ctx",
                                   summary={}, synthesized_at_epoch=float(i))
            e_store.save(exp)
        oc_id = derive_outcome_id("plan2", "ctx_x", OutcomeClassification.FAILURE, ["ev2"], {})
        ev = learn_from_outcome(oc_id, context_id="ctx_x", strategy_id="st_ctx",
                                outcome_store=o_store, strategy_store=s_store,
                                experience_store=e_store, learning_store=l_store,
                                created_at_epoch=10.0)
        # At least one applied adaptation (decrease) should be context-restricted
        self.assertGreater(len(ev.adaptations_applied), 0)
        for adapt in ev.adaptations_applied:
            self.assertIsNotNone(adapt.context_restriction)
            self.assertIn("allowed_contexts", adapt.context_restriction)
            self.assertIn("ctx_x", adapt.context_restriction["allowed_contexts"])
        # All proposed negative adaptations should carry a restriction
        for adapt in ev.adaptations_proposed:
            self.assertIsNotNone(adapt.context_restriction)

    def test_confidence_decrease_is_context_scoped(self):
        s_store = StrategyStore(":memory:")
        o_store = OutcomeStore(":memory:")
        e_store = ExperienceStore(":memory:")
        l_store = LearningStore(":memory:")
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        strat = Strategy(strategy_id="st_ctx2", name="ctx2", description="d",
                         problem_class="bug_fix", tool_sequence=["file.read"],
                         confidence=0.7, strategy_type="explicit",
                         created_at_epoch=1.0, updated_at_epoch=1.0)
        s_store.save(strat)
        for i in range(3):
            oc = Outcome(outcome_id=derive_outcome_id(f"p{i}", "ctx_y",
                          OutcomeClassification.FAILURE, [f"ev{i}"], {}),
                         plan_id=f"p{i}", context_id="ctx_y",
                         classification=OutcomeClassification.FAILURE,
                         verification_evidence_ids=(f"ev{i}",), created_at_epoch=float(i))
            o_store.save(oc)
            exp = ExperienceRecord(experience_id=derive_experience_id(f"t{i}", "ctx_y", oc.outcome_id, [f"ev{i}"], "st_ctx2"),
                                   task_id=f"t{i}", task_type="bug_fix", domain="lint",
                                   context_id="ctx_y", outcome_id=oc.outcome_id,
                                   evidence_ids=(f"ev{i}",), strategy_id="st_ctx2",
                                   summary={}, synthesized_at_epoch=float(i))
            e_store.save(exp)
        oc_id = derive_outcome_id("p2", "ctx_y", OutcomeClassification.FAILURE, ["ev2"], {})
        ev = learn_from_outcome(oc_id, context_id="ctx_y", strategy_id="st_ctx2",
                                outcome_store=o_store, strategy_store=s_store,
                                experience_store=e_store, learning_store=l_store,
                                created_at_epoch=10.0)
        # Context restriction persisted on the strategy
        updated = s_store.get("st_ctx2")
        self.assertIn("allowed_contexts", updated.context_restrictions)
        self.assertIn("ctx_y", updated.context_restrictions["allowed_contexts"])
