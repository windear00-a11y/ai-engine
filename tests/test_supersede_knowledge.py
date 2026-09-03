"""Phase 5: knowledge supersede (propose -> approve)."""

import unittest

from intelligence.knowledge.events import LifecycleEventStore
from intelligence.knowledge.lifecycle import (
    approve_supersede_knowledge,
    propose_supersede_knowledge,
)
from retrieval.repository import KnowledgeRepository


class SupersedeKnowledgeTests(unittest.TestCase):
    def setUp(self):
        self.repo = KnowledgeRepository(":memory:")
        self.repo.initialize()
        self.store = LifecycleEventStore(":memory:")
        self.addCleanup(self.repo.close)
        self.addCleanup(self.store.close)
        self.repo.add_node("old1", "fact", "Old rule", "v1")
        self.repo.add_node("new1", "fact", "New rule", "v2")

    def test_propose_does_not_mutate_nodes(self):
        propose_supersede_knowledge("old1", "new1", ["ev_1"], self.store,
                                    created_at_epoch=1.0)
        self.assertNotIn("lifecycle",
                         self.repo.get_node("old1"))
        self.assertNotIn("lifecycle",
                         self.repo.get_node("new1"))

    def test_propose_records_pending_event(self):
        event = propose_supersede_knowledge("old1", "new1", ["ev_1"],
                                            self.store, created_at_epoch=1.0)
        self.assertEqual(event.status, "pending")
        self.assertEqual(event.event_type, "supersede")
        self.assertEqual(event.new_value["superseded_by"], "new1")

    def test_approve_marks_old_superseded(self):
        approve_supersede_knowledge("old1", "new1", ["ev_1"], self.repo,
                                    self.store, created_at_epoch=2.0)
        old_life = self.repo.get_node("old1")["lifecycle"]
        self.assertEqual(old_life["status"], "superseded")
        self.assertEqual(old_life["superseded_by"], "new1")

    def test_approve_marks_new_active(self):
        approve_supersede_knowledge("old1", "new1", ["ev_1"], self.repo,
                                    self.store, created_at_epoch=2.0)
        new_life = self.repo.get_node("new1")["lifecycle"]
        self.assertEqual(new_life["status"], "active")
        self.assertEqual(new_life["supersedes"], "old1")

    def test_approve_records_applied_event(self):
        event = approve_supersede_knowledge("old1", "new1", ["ev_1"],
                                            self.repo, self.store,
                                            created_at_epoch=2.0)
        self.assertEqual(event.status, "applied")
        self.assertEqual(event.old_value["superseded_by"], None)
        self.assertEqual(event.new_value["superseded_by"], "new1")

    def test_reapproval_keeps_final_state_stable(self):
        approve_supersede_knowledge("old1", "new1", ["ev_1"], self.repo,
                                    self.store, created_at_epoch=2.0)
        approve_supersede_knowledge("old1", "new1", ["ev_1"], self.repo,
                                    self.store, created_at_epoch=3.0)
        old_life = self.repo.get_node("old1")["lifecycle"]
        self.assertEqual(old_life["status"], "superseded")
        self.assertEqual(old_life["superseded_by"], "new1")

    def test_missing_old_node_raises(self):
        with self.assertRaises(KeyError):
            approve_supersede_knowledge("ghost", "new1", ["ev"], self.repo,
                                        self.store)

    def test_missing_new_node_raises(self):
        with self.assertRaises(KeyError):
            approve_supersede_knowledge("old1", "ghost", ["ev"], self.repo,
                                        self.store)


if __name__ == "__main__":
    unittest.main()
