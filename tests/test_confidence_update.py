"""Phase 5: knowledge confidence updates recorded with evidence chain."""

import unittest

from intelligence.knowledge.confidence import (
    base_confidence,
    get_knowledge_confidence,
    update_knowledge_confidence,
)
from intelligence.knowledge.events import LifecycleEventStore
from retrieval.repository import KnowledgeRepository


class KnowledgeConfidenceUpdateTests(unittest.TestCase):
    def setUp(self):
        self.repo = KnowledgeRepository(":memory:")
        self.repo.initialize()
        self.store = LifecycleEventStore(":memory:")
        self.addCleanup(self.repo.close)
        self.addCleanup(self.store.close)
        self.repo.add_node("n1", "fact", "A claim", "desc")
        self.node = self.repo.get_node("n1")

    def test_confidence_stored_and_readable(self):
        update_knowledge_confidence(self.node, 0.8, ["ev_1"], self.repo,
                                    self.store, created_at_epoch=1.0)
        self.assertAlmostEqual(
            get_knowledge_confidence(self.repo.get_node("n1")), 0.8)

    def test_clamped_to_bounds(self):
        update_knowledge_confidence(self.node, 5.0, [], self.repo,
                                    self.store, created_at_epoch=1.0)
        self.assertAlmostEqual(
            get_knowledge_confidence(self.repo.get_node("n1")), 1.0)

    def test_change_audited_with_evidence_chain(self):
        update_knowledge_confidence(self.node, 0.7, ["ev_a", "ev_b"],
                                    self.repo, self.store,
                                    created_at_epoch=1.0)
        events = self.store.for_knowledge("n1")
        self.assertEqual(len(events), 1)
        ev = events[0]
        self.assertEqual(ev.event_type, "confidence_update")
        self.assertEqual(sorted(ev.evidence_ids), ["ev_a", "ev_b"])
        self.assertEqual(ev.old_value["confidence"], 0.0)
        self.assertEqual(ev.new_value["confidence"], 0.7)

    def test_history_accumulates(self):
        update_knowledge_confidence(self.node, 0.5, ["a"], self.repo,
                                    self.store, created_at_epoch=1.0)
        current = self.repo.get_node("n1")
        update_knowledge_confidence(current, 0.9, ["b"], self.repo,
                                    self.store, created_at_epoch=2.0)
        life = self.repo.get_node("n1")["lifecycle"]
        self.assertEqual(len(life["confidence_history"]), 2)

    def test_base_confidence_from_support_counts(self):
        strong = {"support_count": 10}
        weak = {"support_count": 1, "contradict_count": 8}
        s = base_confidence({"support_count": 10})
        w = base_confidence({"support_count": 1, "contradict_count": 8})
        self.assertGreater(s, w)
        self.assertTrue(0.0 <= s <= 1.0)

    def test_default_when_none_provided(self):
        self.repo.add_node("n2", "fact", "B", "d",
                           metadata={"support_count": 4})
        event = update_knowledge_confidence(self.repo.get_node("n2"), None,
                                            ["ev"], self.repo, self.store,
                                            created_at_epoch=1.0)
        self.assertGreaterEqual(event.new_value["confidence"], 0.0)


if __name__ == "__main__":
    unittest.main()
