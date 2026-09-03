"""Phase 5: lifecycle events are immutable (append-only)."""

import unittest

from intelligence.knowledge.events import (
    KnowledgeLifecycleEvent,
    LifecycleEventStore,
    derive_event_id,
)


class LifecycleEventsImmutableTests(unittest.TestCase):
    def setUp(self):
        self.store = LifecycleEventStore(":memory:")
        self.addCleanup(self.store.close)

    def _insert(self):
        event = KnowledgeLifecycleEvent(
            event_id=derive_event_id("n1", "supersede", {}, {"a": 1},
                                     ["ev_1"], 1.0),
            knowledge_id="n1", event_type="supersede",
            old_value={}, new_value={"a": 1}, evidence_ids=["ev_1"],
            status="applied", note=None, created_at_epoch=1.0)
        self.store.save(event)
        return event

    def test_event_roundtrip(self):
        event = self._insert()
        got = self.store.get(event.event_id)
        self.assertEqual(got.knowledge_id, "n1")
        self.assertEqual(got.event_type, "supersede")
        self.assertEqual(got.new_value, {"a": 1})
        self.assertEqual(got.evidence_ids, ("ev_1",))

    def test_event_id_deterministic(self):
        e1 = self._insert()
        e2 = self._insert()
        self.assertEqual(len(self.store.all()), 1)

    def test_update_rejected(self):
        self._insert()
        with self.assertRaises(Exception):
            self.store.conn.execute(
                "UPDATE knowledge_lifecycle_events SET note='x'")

    def test_delete_rejected(self):
        self._insert()
        with self.assertRaises(Exception):
            self.store.conn.execute(
                "DELETE FROM knowledge_lifecycle_events")

    def test_events_remain_after_rejected_mutation(self):
        self._insert()
        try:
            self.store.conn.execute(
                "UPDATE knowledge_lifecycle_events SET note='x'")
        except Exception:
            pass
        self.assertEqual(len(self.store.all()), 1)


if __name__ == "__main__":
    unittest.main()
