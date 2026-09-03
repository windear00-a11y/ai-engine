"""Phase 2: evidence records are immutable (append-only)."""

import sqlite3
import unittest

from intelligence.evidence import EvidenceType
from intelligence.evidence.schema import EvidenceRecord
from intelligence.evidence.store import EvidenceStore


def _rec(**kw):
    return EvidenceRecord(
        evidence_id=kw.get("evidence_id", "ev_x"),
        source_observation_id=kw.get("source_observation_id", "obs"),
        claim=kw.get("claim", "claim"),
        context_id=kw.get("context_id", None),
        evidence_type=kw.get("evidence_type", EvidenceType.FACT),
        supporting_data=kw.get("supporting_data", {}),
        created_at_epoch=kw.get("created_at_epoch", 0.0),
    )


class EvidenceImmutableTests(unittest.TestCase):
    def setUp(self):
        self.store = EvidenceStore(":memory:")
        self.addCleanup(self.store.close)
        self.store.save(_rec())

    def test_update_rejected(self):
        try:
            self.store.conn.execute(
                "UPDATE evidence SET claim='hacked' WHERE evidence_id='ev_x'")
            self.store.conn.commit()
            self.fail("UPDATE should have been rejected")
        except sqlite3.DatabaseError:
            pass

    def test_delete_rejected(self):
        try:
            self.store.conn.execute(
                "DELETE FROM evidence WHERE evidence_id='ev_x'")
            self.store.conn.commit()
            self.fail("DELETE should have been rejected")
        except sqlite3.DatabaseError:
            pass

    def test_record_still_present_after_update_attempt(self):
        rec = self.store.get("ev_x")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.claim, "claim")


if __name__ == "__main__":
    unittest.main()
