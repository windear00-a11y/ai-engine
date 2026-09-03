"""Phase 2: evidence recording is deterministic (same inputs -> same id)."""

import unittest

from intelligence.evidence import EvidenceType, record_evidence


class EvidenceRecordDeterministicTests(unittest.TestCase):
    def test_same_inputs_same_id(self):
        a = record_evidence("obs-1", "claim A", None, EvidenceType.FACT,
                            {"x": 1})
        b = record_evidence("obs-1", "claim A", None, EvidenceType.FACT,
                            {"x": 1})
        self.assertEqual(a.evidence_id, b.evidence_id)

    def test_different_claim_differs(self):
        a = record_evidence("obs-1", "claim A", None, EvidenceType.FACT)
        b = record_evidence("obs-1", "claim B", None, EvidenceType.FACT)
        self.assertNotEqual(a.evidence_id, b.evidence_id)

    def test_different_supporting_data_differs(self):
        a = record_evidence("obs-1", "claim", None, EvidenceType.FACT,
                            {"v": 1})
        b = record_evidence("obs-1", "claim", None, EvidenceType.FACT,
                            {"v": 2})
        self.assertNotEqual(a.evidence_id, b.evidence_id)

    def test_every_evidence_type_is_supported(self):
        for etype in EvidenceType:
            rec = record_evidence("obs", "claim", None, etype)
            self.assertEqual(rec.evidence_type, etype)


if __name__ == "__main__":
    unittest.main()
