"""Phase 3: experience synthesis is deterministic (same inputs -> same id)."""

import unittest

from intelligence.evidence import EvidenceType, record_evidence
from intelligence.experience import synthesize_experience
from intelligence.outcome import record_outcome
from intelligence.outcome.types import OutcomeClassification


class ExperienceSynthesisDeterministicTests(unittest.TestCase):
    def test_same_inputs_same_id(self):
        ev = record_evidence("obs", "claim", "ctx-1", EvidenceType.FACT,
                             {"ok": True})
        oc = record_outcome("t1", "ctx-1", OutcomeClassification.SUCCESS,
                            [ev.evidence_id])
        a = synthesize_experience("t1", "ctx-1", oc.outcome_id,
                                  [ev.evidence_id], task_type="bug_fix")
        b = synthesize_experience("t1", "ctx-1", oc.outcome_id,
                                  [ev.evidence_id], task_type="bug_fix")
        self.assertEqual(a.experience_id, b.experience_id)

    def test_different_strategy_differs(self):
        ev = record_evidence("obs", "claim", "ctx-1", EvidenceType.FACT)
        oc = record_outcome("t1", "ctx-1", OutcomeClassification.SUCCESS,
                            [ev.evidence_id])
        a = synthesize_experience("t1", "ctx-1", oc.outcome_id,
                                  [ev.evidence_id], strategy_id="strat-A")
        b = synthesize_experience("t1", "ctx-1", oc.outcome_id,
                                  [ev.evidence_id], strategy_id="strat-B")
        self.assertNotEqual(a.experience_id, b.experience_id)

    def test_different_task_differs(self):
        ev = record_evidence("obs", "claim", "ctx-1", EvidenceType.FACT)
        oc = record_outcome("t1", "ctx-1", OutcomeClassification.SUCCESS,
                            [ev.evidence_id])
        a = synthesize_experience("t1", "ctx-1", oc.outcome_id,
                                  [ev.evidence_id])
        b = synthesize_experience("t2", "ctx-1", oc.outcome_id,
                                  [ev.evidence_id])
        self.assertNotEqual(a.experience_id, b.experience_id)

    def test_requires_context(self):
        ev = record_evidence("obs", "claim", "ctx-1", EvidenceType.FACT)
        oc = record_outcome("t1", "ctx-1", OutcomeClassification.SUCCESS,
                            [ev.evidence_id])
        with self.assertRaises(ValueError):
            synthesize_experience("t1", "", oc.outcome_id, [ev.evidence_id])


if __name__ == "__main__":
    unittest.main()
