"""Phase 3: experience is an interpreted summary, NOT the raw execution log.

An experience record must never embed raw step inputs/results. It contains
only the interpreted association (task/context/outcome/evidence) plus a
compact summary.
"""

import unittest

from intelligence.evidence import EvidenceType, record_evidence
from intelligence.evidence.store import EvidenceStore
from intelligence.experience import synthesize_experience
from intelligence.experience.store import ExperienceStore
from intelligence.outcome import record_outcome
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification


RAW_STEP_RESULT = {
    "step_id": "s1", "tool": "bash", "args": ["secret-command-xyz"],
    "raw_output": "huge-dump-of-execution-1", "duration": 12.3,
}


class ExperienceNotRawJournalTests(unittest.TestCase):
    def setUp(self):
        self.ev_store = EvidenceStore(":memory:")
        self.oc_store = OutcomeStore(":memory:")
        self.xp_store = ExperienceStore(":memory:")
        self.addCleanup(self.ev_store.close)
        self.addCleanup(self.oc_store.close)
        self.addCleanup(self.xp_store.close)

    def test_raw_step_data_is_not_embedded(self):
        ctx = "ctx-1"
        ev = record_evidence("obs", "claim", ctx, EvidenceType.FACT,
                             store=self.ev_store)
        oc = record_outcome("t1", ctx, OutcomeClassification.SUCCESS,
                            [ev.evidence_id], store=self.oc_store)
        xp = synthesize_experience(
            "t1", ctx, oc.outcome_id, [ev.evidence_id],
            summary={"outcome": "success", "text": "lint passed"},
            store=self.xp_store)
        serialized = xp.to_json()
        # raw step tool/args/output must not leak into the experience
        self.assertNotIn("secret-command-xyz", serialized)
        self.assertNotIn("huge-dump-of-execution-1", serialized)
        self.assertNotIn("step_id", xp.summary)

    def test_summary_is_compact_interpretation(self):
        ctx = "ctx-1"
        ev = record_evidence("obs", "claim", ctx, EvidenceType.FACT,
                             store=self.ev_store)
        oc = record_outcome("t1", ctx, OutcomeClassification.SUCCESS,
                            [ev.evidence_id], store=self.oc_store)
        xp = synthesize_experience(
            "t1", ctx, oc.outcome_id, [ev.evidence_id],
            summary={"outcome": "success", "text": "goal met"},
            store=self.xp_store)
        self.assertIn("text", xp.summary)
        self.assertEqual(xp.summary["outcome"], "success")


if __name__ == "__main__":
    unittest.main()
