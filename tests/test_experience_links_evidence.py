"""Phase 3: experience records reference their supporting evidence chain."""

import unittest

from intelligence.evidence import EvidenceType, record_evidence
from intelligence.evidence.store import EvidenceStore
from intelligence.experience import synthesize_experience
from intelligence.experience.store import ExperienceStore
from intelligence.outcome import record_outcome
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification


class ExperienceLinksEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.ev_store = EvidenceStore(":memory:")
        self.oc_store = OutcomeStore(":memory:")
        self.xp_store = ExperienceStore(":memory:")
        self.addCleanup(self.ev_store.close)
        self.addCleanup(self.oc_store.close)
        self.addCleanup(self.xp_store.close)

    def test_record_references_evidence_ids(self):
        ctx = "ctx-1"
        ev1 = record_evidence("obs1", "claim A", ctx, EvidenceType.FACT,
                              store=self.ev_store)
        ev2 = record_evidence("obs2", "claim B", ctx,
                              EvidenceType.HEURISTIC, store=self.ev_store)
        oc = record_outcome("t1", ctx, OutcomeClassification.SUCCESS,
                            [ev1.evidence_id, ev2.evidence_id],
                            store=self.oc_store)
        xp = synthesize_experience(
            "t1", ctx, oc.outcome_id,
            [ev1.evidence_id, ev2.evidence_id], store=self.xp_store)
        self.assertEqual(set(xp.evidence_ids),
                         {ev1.evidence_id, ev2.evidence_id})
        stored = self.xp_store.get(xp.experience_id)
        self.assertEqual(set(stored.evidence_ids),
                         {ev1.evidence_id, ev2.evidence_id})


if __name__ == "__main__":
    unittest.main()
