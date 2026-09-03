"""Phase 3: experience records reference their outcome."""

import unittest

from intelligence.evidence import EvidenceType, record_evidence
from intelligence.evidence.store import EvidenceStore
from intelligence.experience import synthesize_experience
from intelligence.experience.store import ExperienceStore
from intelligence.outcome import record_outcome
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification


class ExperienceLinksOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.ev_store = EvidenceStore(":memory:")
        self.oc_store = OutcomeStore(":memory:")
        self.xp_store = ExperienceStore(":memory:")
        self.addCleanup(self.ev_store.close)
        self.addCleanup(self.oc_store.close)
        self.addCleanup(self.xp_store.close)

    def test_record_references_outcome(self):
        ctx = "ctx-1"
        ev = record_evidence("obs", "claim", ctx, EvidenceType.FACT,
                             store=self.ev_store)
        oc = record_outcome("t1", ctx, OutcomeClassification.SUCCESS,
                            [ev.evidence_id], store=self.oc_store)
        xp = synthesize_experience("t1", ctx, oc.outcome_id,
                                   [ev.evidence_id], store=self.xp_store)
        self.assertEqual(xp.outcome_id, oc.outcome_id)
        stored = self.xp_store.get(xp.experience_id)
        self.assertEqual(stored.outcome_id, oc.outcome_id)

    def test_synthesize_requires_outcome(self):
        with self.assertRaises(ValueError):
            synthesize_experience("t1", "ctx-1", "", [], store=self.xp_store)


if __name__ == "__main__":
    unittest.main()
