"""Phase 5: contradictory knowledge detected deterministically."""

import unittest

from intelligence.knowledge.conflict import detect_conflicts


class ConflictDetectionTests(unittest.TestCase):
    @staticmethod
    def _node(nid, ntype="fact", name=None, subject=None,
              lifecycle=None, active=True):
        meta = {}
        if subject is not None:
            meta["subject"] = subject
        if lifecycle:
            meta["lifecycle"] = lifecycle
        node = {"id": nid, "type": ntype, "name": name or nid,
                "description": "d"}
        node.update(meta)
        return node

    def test_duplicate_active_subject_detected(self):
        nodes = [self._node("a", subject="algorithm-x"),
                 self._node("b", subject="algorithm-x")]
        conflicts = detect_conflicts(nodes)
        self.assertEqual(len(conflicts), 1)
        c = conflicts[0]
        self.assertEqual(c.conflict_type, "duplicate_subject")
        self.assertEqual({c.node_a_id, c.node_b_id}, {"a", "b"})

    def test_superseded_does_not_flag_duplicate(self):
        nodes = [self._node("a", subject="x",
                            lifecycle={"status": "superseded"}),
                 self._node("b", subject="x")]
        conflicts = detect_conflicts(nodes)
        self.assertEqual(conflicts, [])

    def test_mixed_lifecycle_conflict(self):
        nodes = [self._node("a", lifecycle={
                    "status": "invalidated", "superseded_by": "b"}),
                 self._node("b")]
        conflicts = detect_conflicts(nodes)
        self.assertTrue(any(c.conflict_type == "mixed_lifecycle"
                            for c in conflicts))

    def test_supersession_conflict_when_successor_superseded(self):
        nodes = [self._node("a", lifecycle={
                    "status": "superseded", "superseded_by": "b"}),
                 self._node("b", lifecycle={"status": "superseded"})]
        conflicts = detect_conflicts(nodes)
        self.assertTrue(any(c.conflict_type == "supersession"
                            for c in conflicts))

    def test_deterministic_ordering(self):
        nodes = [self._node("a", subject="z"),
                 self._node("b", subject="z"),
                 self._node("c", subject="z")]
        c1 = detect_conflicts(nodes)
        c2 = detect_conflicts(nodes)
        keys1 = [(x.node_a_id, x.node_b_id) for x in c1]
        keys2 = [(x.node_a_id, x.node_b_id) for x in c2]
        self.assertEqual(keys1, keys2)

    def test_knowledge_ids_filter(self):
        nodes = [self._node("a", subject="x"),
                 self._node("b", subject="x"),
                 self._node("c", subject="y"),
                 self._node("d", subject="y")]
        conflicts = detect_conflicts(nodes, knowledge_ids=["a", "b"])
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].conflict_type, "duplicate_subject")


if __name__ == "__main__":
    unittest.main()
