"""Phase 5: knowledge invalidation (propose -> approve)."""

import unittest

from intelligence.knowledge.events import LifecycleEventStore
from intelligence.knowledge.lifecycle import (
    approve_invalidate_knowledge,
    propose_invalidate_knowledge,
)
from retrieval.repository import KnowledgeRepository


class InvalidateKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.repo = KnowledgeRepository(":memory:")
        self.repo.initialize()
        self.store = LifecycleEventStore(":memory:")
        self.addCleanup(self.repo.close)
        self.addCleanup(self.store.close)
        self.repo.add_node("n1", "fact", "A claim", "desc")

    def test_propose_does_not_mutate(self):
        propose_invalidate_knowledge("n1", "outdated", ["ev_1"], self.store,
                                     created_at_epoch=1.0)
        self.assertNotIn("lifecycle", self.repo.get_node("n1"))

    def test_propose_records_pending_with_reason(self):
        event = propose_invalidate_knowledge("n1", "outdated info",
                                             ["ev_1"], self.store,
                                             created_at_epoch=1.0)
        self.assertEqual(event.status, "pending")
        self.assertEqual(event.new_value["reason"], "outdated info")

    def test_approve_marks_invalidated(self):
        approve_invalidate_knowledge("n1", "outdated info", ["ev_1"],
                                     self.repo, self.store,
                                     created_at_epoch=2.0)
        life = self.repo.get_node("n1")["lifecycle"]
        self.assertEqual(life["status"], "invalidated")
        self.assertEqual(life["reason"], "outdated info")

    def test_approve_records_applied_event(self):
        event = approve_invalidate_knowledge("n1", "outdated info", ["ev_1"],
                                             self.repo, self.store,
                                             created_at_epoch=2.0)
        self.assertEqual(event.status, "applied")
        self.assertEqual(event.new_value["status"], "invalidated")
        self.assertIn("ev_1", event.evidence_ids)

    def test_missing_node_raises(self):
        with self.assertRaises(KeyError):
            approve_invalidate_knowledge("ghost", "reason", ["ev"],
                                         self.repo, self.store)


if __name__ == "__main__":
    unittest.main()
