"""Phase 3: experience records are immutable (append-only)."""

import sqlite3
import unittest

from intelligence.experience.schema import ExperienceRecord
from intelligence.experience.store import ExperienceStore
from intelligence.outcome.types import OutcomeClassification


def _rec():
    return ExperienceRecord(
        experience_id="xp_x",
        task_id="t1",
        task_type="bug_fix",
        domain="coding",
        context_id="ctx-1",
        outcome_id="oc_1",
        evidence_ids=("ev_1",),
        summary={"outcome": OutcomeClassification.SUCCESS.value},
        synthesized_at_epoch=0.0,
    )


class ExperienceImmutableTests(unittest.TestCase):
    def setUp(self):
        self.store = ExperienceStore(":memory:")
        self.addCleanup(self.store.close)
        self.store.save(_rec())

    def test_update_rejected(self):
        try:
            self.store.conn.execute(
                "UPDATE experience SET task_type='refactor' "
                "WHERE experience_id='xp_x'")
            self.store.conn.commit()
            self.fail("UPDATE should have been rejected")
        except sqlite3.DatabaseError:
            pass

    def test_delete_rejected(self):
        try:
            self.store.conn.execute(
                "DELETE FROM experience WHERE experience_id='xp_x'")
            self.store.conn.commit()
            self.fail("DELETE should have been rejected")
        except sqlite3.DatabaseError:
            pass

    def test_record_survives_mutation_attempt(self):
        rec = self.store.get("xp_x")
        self.assertIsNotNone(rec)
        self.assertEqual(rec.task_type, "bug_fix")


if __name__ == "__main__":
    unittest.main()
