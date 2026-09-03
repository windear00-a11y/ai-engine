"""Phase 6: contradictions are detected and reported (never auto-resolved)."""

import unittest

from intelligence.reasoning import (
    detect_knowledge_contradictions,
    detect_experience_contradictions,
    detect_all_contradictions,
)

_CTX = {"context_id": "ctx_a"}


class _Exp:
    def __init__(self, task_type, outcome, eid, context_id="ctx_a"):
        self.task_type = task_type
        self.domain = "lint"
        self.context_id = context_id
        self.summary = {"outcome": outcome, "context_match": 0.95}
        self.experience_id = eid


class ReasoningContradictionTests(unittest.TestCase):
    def test_knowledge_opposition_detected(self):
        nodes = [
            {"id": "k1", "type": "fact", "name": "run full test suite",
             "subject": "testing"},
            {"id": "k2", "type": "fact", "name": "do not run full test suite",
             "subject": "testing"},
        ]
        culprits = detect_knowledge_contradictions(nodes)
        self.assertEqual(len(culprits), 1)
        self.assertEqual(culprits[0].contradiction_type, "knowledge")
        self.assertEqual({culprits[0].node_a, culprits[0].node_b},
                         {"k1", "k2"})

    def test_experience_success_and_failure_detected(self):
        exps = [_Exp("test_verify", "success_verified", "a"),
                _Exp("test_verify", "failure", "b")]
        culprits = detect_experience_contradictions(exps)
        self.assertEqual(len(culprits), 1)
        self.assertEqual(culprits[0].contradiction_type, "experience")
        self.assertEqual(culprits[0].node_a, "test_verify")

    def test_all_success_no_contradiction(self):
        exps = [_Exp("test_verify", "success_verified", "a"),
                _Exp("test_verify", "success_verified", "b")]
        self.assertEqual(detect_experience_contradictions(exps), [])

    def test_deterministic(self):
        nodes = [
            {"id": "k1", "type": "fact", "name": "run full test suite",
             "subject": "testing"},
            {"id": "k2", "type": "fact", "name": "do not run full test suite",
             "subject": "testing"},
        ]
        a = detect_knowledge_contradictions(nodes)
        b = detect_knowledge_contradictions(nodes)
        self.assertEqual([x.to_dict() for x in a], [x.to_dict() for x in b])

    def test_reported_not_auto_resolved(self):
        nodes = [
            {"id": "k1", "type": "fact", "name": "assertX is best",
             "subject": "assertion"},
            {"id": "k2", "type": "fact", "name": "do not assertX",
             "subject": "assertion"},
        ]
        culprits = detect_all_contradictions(nodes, [])
        # contradictions are reported as objects -- the nodes themselves are
        # not modified / no resolution is applied.
        self.assertEqual(len(culprits), 1)
        live_nodes = {n["id"] for n in nodes}
        self.assertEqual(len(live_nodes), 2)


if __name__ == "__main__":
    unittest.main()
