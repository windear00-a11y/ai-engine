"""Phase 8: learning is deterministic."""

import unittest

from intelligence.strategy.schema import Strategy
from intelligence.strategy.store import StrategyStore
from intelligence.outcome.schema import Outcome, derive_outcome_id
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification
from intelligence.experience.schema import ExperienceRecord, derive_experience_id
from intelligence.experience.store import ExperienceStore
from intelligence.learning import learn_from_outcome, LearningStore


def _setup_success_stores():
    s_store = StrategyStore(":memory:")
    o_store = OutcomeStore(":memory:")
    e_store = ExperienceStore(":memory:")
    l_store = LearningStore(":memory:")
    strat = Strategy(strategy_id="st_det", name="det", description="d",
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
        exp = ExperienceRecord(experience_id=derive_experience_id(f"t{i}", "ctx_a", oc.outcome_id, [f"ev{i}"], "st_det"),
                               task_id=f"t{i}", task_type="bug_fix", domain="lint",
                               context_id="ctx_a", outcome_id=oc.outcome_id,
                               evidence_ids=(f"ev{i}",), strategy_id="st_det",
                               summary={}, synthesized_at_epoch=float(i))
        e_store.save(exp)
    return s_store, o_store, e_store, l_store


class LearningDeterministicTests(unittest.TestCase):
    def test_same_inputs_same_event(self):
        s_store, o_store, e_store, l_store = _setup_success_stores()
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        oc_id = derive_outcome_id("plan1", "ctx_a", OutcomeClassification.SUCCESS, ["ev1"], {})
        a = learn_from_outcome(oc_id, context_id="ctx_a", strategy_id="st_det",
                               outcome_store=o_store, strategy_store=s_store,
                               experience_store=e_store, learning_store=l_store,
                               created_at_epoch=10.0)
        b = learn_from_outcome(oc_id, context_id="ctx_a", strategy_id="st_det",
                               outcome_store=o_store, strategy_store=s_store,
                               experience_store=e_store, learning_store=l_store,
                               created_at_epoch=10.0)
        self.assertEqual(a.learning_event_id, b.learning_event_id)
        self.assertEqual(a.pattern_detected, b.pattern_detected)
        self.assertEqual(len(a.adaptations_proposed), len(b.adaptations_proposed))

    def test_learning_event_id_prefix(self):
        s_store, o_store, e_store, l_store = _setup_success_stores()
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        oc_id = derive_outcome_id("plan1", "ctx_a", OutcomeClassification.SUCCESS, ["ev1"], {})
        ev = learn_from_outcome(oc_id, context_id="ctx_a", strategy_id="st_det",
                                outcome_store=o_store, strategy_store=s_store,
                                experience_store=e_store, learning_store=l_store,
                                created_at_epoch=10.0)
        self.assertTrue(ev.learning_event_id.startswith("le_"))
        self.assertEqual(len(ev.learning_event_id), 3 + 32)

    def test_serializable(self):
        s_store, o_store, e_store, l_store = _setup_success_stores()
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        oc_id = derive_outcome_id("plan1", "ctx_a", OutcomeClassification.SUCCESS, ["ev1"], {})
        ev = learn_from_outcome(oc_id, context_id="ctx_a", strategy_id="st_det",
                                outcome_store=o_store, strategy_store=s_store,
                                experience_store=e_store, learning_store=l_store,
                                created_at_epoch=10.0)
        d = ev.to_dict()
        self.assertIn("learning_event_id", d)
        self.assertIn("adaptations_proposed", d)
        self.assertIn("evidence_chain", d)
