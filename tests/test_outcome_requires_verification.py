"""Phase 2: an outcome cannot be recorded without verification evidence."""

import unittest

from intelligence.evidence import EvidenceType, record_evidence
from intelligence.outcome import (
    VerificationRequiredError,
    get_outcome,
    record_outcome,
)


class OutcomeRequiresVerificationTests(unittest.TestCase):
    def test_empty_verification_rejected(self):
        with self.assertRaises(VerificationRequiredError):
            record_outcome("plan-1", "ctx", "success", [])

    def test_none_verification_rejected(self):
        with self.assertRaises(VerificationRequiredError):
            record_outcome("plan-1", "ctx", "success", None)

    def test_with_verification_succeeds_and_is_stored(self):
        ev = record_evidence("obs", "claim", None, EvidenceType.FACT,
                             {"ok": True})
        oc = record_outcome("plan-1", "ctx", "success", [ev.evidence_id])
        self.assertEqual(oc.verification_evidence_ids,
                         (ev.evidence_id,))
        stored = get_outcome(oc.outcome_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.classification.value, "success")


if __name__ == "__main__":
    unittest.main()
