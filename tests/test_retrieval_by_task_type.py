"""Phase 3: retrieval filters correctly by task type."""

import unittest

from intelligence.evidence import EvidenceType, record_evidence
from intelligence.evidence.store import EvidenceStore
from intelligence.experience import (
    experience_for_strategy,
    experience_for_task_type,
    synthesize_experience,
)
from intelligence.experience.store import ExperienceStore
from intelligence.outcome import record_outcome
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification


class RetrievalByTaskTypeTests(unittest.TestCase):
    def setUp(self):
        self.ev_store = EvidenceStore(":memory:")
        self.oc_store = OutcomeStore(":memory:")
        self.xp_store = ExperienceStore(":memory:")
        self.addCleanup(self.ev_store.close)
        self.addCleanup(self.oc_store.close)
        self.addCleanup(self.xp_store.close)

    def _make(self, task_id, task_type, strategy=None):
        ev = record_evidence("obs", "claim", "ctx-" + task_id,
                             EvidenceType.FACT, store=self.ev_store)
        oc = record_outcome(task_id, "ctx-" + task_id,
                            OutcomeClassification.SUCCESS, [ev.evidence_id],
                            store=self.oc_store)
        return synthesize_experience(task_id, "ctx-" + task_id,
                                     oc.outcome_id, [ev.evidence_id],
                                     task_type=task_type,
                                     strategy_id=strategy,
                                     store=self.xp_store)

    def test_filters_by_task_type(self):
        self._make("t1", "bug_fix")
        self._make("t2", "refactor")
        bug_fixes = experience_for_task_type("bug_fix", store=self.xp_store)
        self.assertEqual(len(bug_fixes), 1)
        self.assertEqual(bug_fixes[0].task_id, "t1")

    def test_filters_by_strategy(self):
        self._make("t1", "bug_fix", strategy="strat-A")
        self._make("t2", "bug_fix", strategy="strat-B")
        a = experience_for_strategy("strat-A", store=self.xp_store)
        self.assertEqual([r.task_id for r in a], ["t1"])


if __name__ == "__main__":
    unittest.main()
