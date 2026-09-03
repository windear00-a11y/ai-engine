"""Phase 8: learning event has complete evidence chain and audit trail."""

import unittest

from intelligence.strategy.schema import Strategy
from intelligence.strategy.store import StrategyStore
from intelligence.outcome.schema import Outcome, derive_outcome_id
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification
from intelligence.experience.schema import ExperienceRecord, derive_experience_id
from intelligence.experience.store import ExperienceStore
from intelligence.learning import learn_from_outcome, LearningStore


class LearningEventAuditTrailTests(unittest.TestCase):
    def test_evidence_chain_complete(self):
        s_store = StrategyStore(":memory:")
        o_store = OutcomeStore(":memory:")
        e_store = ExperienceStore(":memory:")
        l_store = LearningStore(":memory:")
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        strat = Strategy(strategy_id="st_audit", name="audit", description="d",
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
            exp = ExperienceRecord(experience_id=derive_experience_id(f"t{i}", "ctx_a", oc.outcome_id, [f"ev{i}"], "st_audit"),
                                   task_id=f"t{i}", task_type="bug_fix", domain="lint",
                                   context_id="ctx_a", outcome_id=oc.outcome_id,
                                   evidence_ids=(f"ev{i}",), strategy_id="st_audit",
                                   summary={}, synthesized_at_epoch=float(i))
            e_store.save(exp)
        oc_id = derive_outcome_id("plan1", "ctx_a", OutcomeClassification.SUCCESS, ["ev1"], {})
        ev = learn_from_outcome(oc_id, context_id="ctx_a", strategy_id="st_audit",
                                outcome_store=o_store, strategy_store=s_store,
                                experience_store=e_store, learning_store=l_store,
                                created_at_epoch=10.0)
        self.assertIn("outcome_id", ev.evidence_chain)
        self.assertEqual(ev.evidence_chain["outcome_id"], oc_id)
        self.assertIn("evidence_ids", ev.evidence_chain)
        self.assertIn("ev1", ev.evidence_chain["evidence_ids"])
        self.assertIn("successes", ev.evidence_chain)
        self.assertIn("failures", ev.evidence_chain)
        # Persisted form also has evidence_chain
        row = l_store.get(ev.learning_event_id)
        self.assertIsNotNone(row)
        self.assertIn("outcome_id", row["evidence_chain"])

    def test_adaptations_recorded(self):
        s_store = StrategyStore(":memory:")
        o_store = OutcomeStore(":memory:")
        e_store = ExperienceStore(":memory:")
        l_store = LearningStore(":memory:")
        self.addCleanup(s_store.close); self.addCleanup(o_store.close)
        self.addCleanup(e_store.close); self.addCleanup(l_store.close)
        strat = Strategy(strategy_id="st_audit2", name="audit2", description="d",
                         problem_class="bug_fix", tool_sequence=["file.read"],
                         confidence=0.5, strategy_type="explicit",
                         created_at_epoch=1.0, updated_at_epoch=1.0)
        s_store.save(strat)
        for i in range(3):
            oc = Outcome(outcome_id=derive_outcome_id(f"p{i}", "ctx_a",
                          OutcomeClassification.FAILURE, [f"ev{i}"], {}),
                         plan_id=f"p{i}", context_id="ctx_a",
                         classification=OutcomeClassification.FAILURE,
                         verification_evidence_ids=(f"ev{i}",), created_at_epoch=float(i))
            o_store.save(oc)
            exp = ExperienceRecord(experience_id=derive_experience_id(f"t{i}", "ctx_a", oc.outcome_id, [f"ev{i}"], "st_audit2"),
                                   task_id=f"t{i}", task_type="bug_fix", domain="lint",
                                   context_id="ctx_a", outcome_id=oc.outcome_id,
                                   evidence_ids=(f"ev{i}",), strategy_id="st_audit2",
                                   summary={}, synthesized_at_epoch=float(i))
            e_store.save(exp)
        oc_id = derive_outcome_id("p2", "ctx_a", OutcomeClassification.FAILURE, ["ev2"], {})
        ev = learn_from_outcome(oc_id, context_id="ctx_a", strategy_id="st_audit2",
                                outcome_store=o_store, strategy_store=s_store,
                                experience_store=e_store, learning_store=l_store,
                                created_at_epoch=10.0)
        self.assertGreater(len(ev.adaptations_proposed), 0)
        # Audit trail has pattern
        self.assertIsNotNone(ev.pattern_detected)
        self.assertIn("failure", ev.pattern_detected)
