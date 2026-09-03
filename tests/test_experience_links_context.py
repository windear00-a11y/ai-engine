"""Phase 3: experience records reference their context."""

import unittest

from intelligence.context.schema import ContextSnapshot
from intelligence.evidence import EvidenceType, record_evidence
from intelligence.evidence.store import EvidenceStore
from intelligence.experience import synthesize_experience
from intelligence.experience.store import ExperienceStore
from intelligence.outcome import record_outcome
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification


class ExperienceLinksContextTests(unittest.TestCase):
    def setUp(self):
        self.ev_store = EvidenceStore(":memory:")
        self.oc_store = OutcomeStore(":memory:")
        self.xp_store = ExperienceStore(":memory:")
        self.addCleanup(self.ev_store.close)
        self.addCleanup(self.oc_store.close)
        self.addCleanup(self.xp_store.close)

    def test_record_references_context(self):
        ctx = ContextSnapshot.build(
            system={"os": "linux"}, project={"language": "python"},
            task={"type": "bug_fix"}, temporal={}, captured_at_epoch=0.0)
        ev = record_evidence("obs", "claim", ctx.context_id,
                             EvidenceType.FACT, store=self.ev_store)
        oc = record_outcome("t1", ctx.context_id,
                            OutcomeClassification.SUCCESS, [ev.evidence_id],
                            store=self.oc_store)
        xp = synthesize_experience("t1", ctx.context_id, oc.outcome_id,
                                   [ev.evidence_id], store=self.xp_store)
        self.assertEqual(xp.context_id, ctx.context_id)
        stored = self.xp_store.get(xp.experience_id)
        self.assertEqual(stored.context_id, ctx.context_id)


if __name__ == "__main__":
    unittest.main()
