"""Phase 1: context store is append-only.

No UPDATE or DELETE may be executed on the context_snapshots table. The store
exposes only INSERT and SELECT; schema-level triggers reject UPDATE/DELETE.
"""

import sqlite3
import unittest

from intelligence.context import ContextStore
from intelligence.context.schema import ContextSnapshot


def _snap(**task):
    return ContextSnapshot.build(
        system={"os": "linux"}, project={"language": "python"},
        task=task, temporal={}, captured_at_epoch=1.0,
    )


class ContextStoreAppendOnlyTests(unittest.TestCase):
    def setUp(self):
        self.store = ContextStore(":memory:")
        self.addCleanup(self.store.close)

    def test_insert_then_get(self):
        s = _snap(type="bug_fix")
        self.store.save(s)
        self.assertIsNotNone(self.store.get(s.context_id))

    def test_update_is_rejected(self):
        s = _snap(type="bug_fix")
        self.store.save(s)
        try:
            self.store.conn.execute(
                "UPDATE context_snapshots SET system_json='{}' "
                "WHERE context_id=?", (s.context_id,))
            self.store.conn.commit()
            self.fail("UPDATE should have been rejected")
        except sqlite3.DatabaseError:
            pass

    def test_delete_is_rejected(self):
        s = _snap(type="bug_fix")
        self.store.save(s)
        try:
            self.store.conn.execute(
                "DELETE FROM context_snapshots WHERE context_id=?",
                (s.context_id,))
            self.store.conn.commit()
            self.fail("DELETE should have been rejected")
        except sqlite3.DatabaseError:
            pass

    def test_double_insert_is_idempotent(self):
        s = _snap(type="bug_fix")
        self.store.save(s)
        self.store.save(s)
        self.assertEqual(self.store.count(), 1)


if __name__ == "__main__":
    unittest.main()
