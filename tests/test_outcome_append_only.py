"""Phase 2: outcomes table is append-only (no UPDATE / DELETE)."""

import sqlite3
import unittest

from intelligence.outcome.schema import Outcome
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification


def _outcome():
    return Outcome(
        outcome_id="oc_x",
        plan_id="plan",
        context_id="ctx",
        classification=OutcomeClassification.SUCCESS,
        verification_evidence_ids=("ev_a",),
        created_at_epoch=0.0,
    )


class OutcomeAppendOnlyTests(unittest.TestCase):
    def setUp(self):
        self.store = OutcomeStore(":memory:")
        self.addCleanup(self.store.close)
        self.store.save(_outcome())

    def test_update_rejected(self):
        try:
            self.store.conn.execute(
                "UPDATE outcomes SET classification='failure' "
                "WHERE outcome_id='oc_x'")
            self.store.conn.commit()
            self.fail("UPDATE should have been rejected")
        except sqlite3.DatabaseError:
            pass

    def test_delete_rejected(self):
        try:
            self.store.conn.execute(
                "DELETE FROM outcomes WHERE outcome_id='oc_x'")
            self.store.conn.commit()
            self.fail("DELETE should have been rejected")
        except sqlite3.DatabaseError:
            pass

    def test_row_survives_mutation_attempt(self):
        rec = self.store.get("oc_x")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.classification, OutcomeClassification.SUCCESS)


if __name__ == "__main__":
    unittest.main()
