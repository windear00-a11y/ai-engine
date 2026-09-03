"""Phase 5: knowledge context restriction."""

import unittest

from intelligence.knowledge.events import LifecycleEventStore
from intelligence.knowledge.lifecycle import restrict_context
from intelligence.knowledge.staleness import detect_staleness
from retrieval.repository import KnowledgeRepository


class ContextRestrictionTests(unittest.TestCase):
    def setUp(self):
        self.repo = KnowledgeRepository(":memory:")
        self.repo.initialize()
        self.store = LifecycleEventStore(":memory:")
        self.addCleanup(self.repo.close)
        self.addCleanup(self.store.close)
        self.repo.add_node("n1", "fact", "A claim", "desc")

    def test_restrict_writes_context_restrictions(self):
        restrict_context("n1", {"allowed_contexts": ["proj-a"]},
                         self.repo, self.store, created_at_epoch=1.0)
        life = self.repo.get_node("n1")["lifecycle"]
        self.assertEqual(life["context_restrictions"],
                         {"allowed_contexts": ["proj-a"]})

    def test_restrict_records_audited_event(self):
        event = restrict_context("n1", {"system": "linux"}, self.repo,
                                 self.store, created_at_epoch=1.0)
        self.assertEqual(event.event_type, "restrict_context")
        self.assertEqual(event.status, "applied")
        self.assertEqual(event.new_value["context_restrictions"],
                         {"system": "linux"})

    def test_missing_node_raises(self):
        with self.assertRaises(KeyError):
            restrict_context("ghost", {}, self.repo, self.store)

    def test_restricted_node_stale_for_foreign_context(self):
        restrict_context("n1", {"allowed_contexts": ["proj-a"]},
                         self.repo, self.store, created_at_epoch=1.0)
        staleness = detect_staleness([self.repo.get_node("n1")],
                                     context_id="proj-b")
        self.assertEqual(len(staleness), 1)
        self.assertIn("context restriction",
                      staleness[0].reasons[0])
        # not stale for its own context
        freshet = detect_staleness([self.repo.get_node("n1")],
                                   context_id="proj-a")
        self.assertEqual(freshet, [])


if __name__ == "__main__":
    unittest.main()
