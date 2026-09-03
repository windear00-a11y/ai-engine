"""Phase 5: outdated knowledge is flagged."""

import unittest

from intelligence.knowledge.staleness import detect_staleness, staleness_score


def _node(nid, lifecycle=None):
    node = {"id": nid, "type": "fact", "name": nid, "description": "d"}
    if lifecycle:
        node["lifecycle"] = lifecycle
    return node


class StalenessDetectionTests(unittest.TestCase):
    def test_superseded_is_stale(self):
        score, reasons = staleness_score(
            _node("a", lifecycle={"status": "superseded"}))
        self.assertEqual(score, 1.0)
        self.assertIn("superseded", reasons)

    def test_invalidated_is_stale(self):
        score, reasons = staleness_score(
            _node("a", lifecycle={"status": "invalidated"}))
        self.assertEqual(score, 1.0)

    def test_pending_flags_stale(self):
        score, _ = staleness_score(
            _node("a", lifecycle={"status": "pending_superseded"}))
        self.assertGreater(score, 0.0)

    def test_low_confidence_stale(self):
        score, reasons = staleness_score(
            _node("a", lifecycle={"confidence": 0.1}))
        self.assertGreater(score, 0.0)
        self.assertTrue(any("low confidence" in r for r in reasons))

    def test_active_fresh_not_stale(self):
        score, reasons = staleness_score(_node("a"))
        self.assertEqual(score, 0.0)
        self.assertEqual(reasons, [])

    def test_detect_staleness_lists_only_stale(self):
        nodes = [_node("fresh"),
                 _node("stale", lifecycle={"status": "superseded"}),
                 _node("low", lifecycle={"confidence": 0.05})]
        records = detect_staleness(nodes)
        ids = [r.knowledge_id for r in records]
        self.assertIn("stale", ids)
        self.assertIn("low", ids)
        self.assertNotIn("fresh", ids)

    def test_deterministic_ordering(self):
        nodes = [_node("x", lifecycle={"status": "superseded"}),
                 _node("y", lifecycle={"status": "invalidated"}),
                 _node("z", lifecycle={"status": "superseded"})]
        r1 = detect_staleness(nodes)
        r2 = detect_staleness(nodes)
        self.assertEqual([r.knowledge_id for r in r1],
                         [r.knowledge_id for r in r2])


if __name__ == "__main__":
    unittest.main()
