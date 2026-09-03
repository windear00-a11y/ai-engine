"""Phase 6: Canonical Model - test strategy scenario with a contradiction.

A test-verification strategy has both succeeded and failed in comparable
contexts; reasoning must report the contradiction without auto-resolving it
and without lending uncontested positive support to the strategy.
"""

import unittest

from intelligence.reasoning import reason, ReasoningQuery


class _Exp:
    def __init__(self, task_type, outcome, context_id, eid):
        self.task_type = task_type
        self.domain = "lint"
        self.context_id = context_id
        self.summary = {"outcome": outcome, "context_match": 0.95}
        self.experience_id = eid


class ExampleTestStrategyContradictionTests(unittest.TestCase):
    def setUp(self):
        self.query = ReasoningQuery(
            question="Should we use the full-suite verification strategy?",
            task_type="test_verify", domain="lint",
            target={"file": "tests/test_utils.py"})
        self.context = {"context_id": "ctx_linux312_flake8"}

    def test_contradiction_detected_and_reported(self):
        experiences = [
            _Exp("test_verify", "success_verified", "ctx_a", "xp_ok"),
            _Exp("test_verify", "failure", "ctx_a", "xp_fail"),
        ]
        out = reason(self.query, self.context, knowledge_nodes=[],
                     experiences=experiences)
        types = [c.contradiction_type for c in out.contradictions]
        self.assertIn("experience", types)
        # contradiction reported, no auto-resolution
        culprit_nodes = {c.node_a for c in out.contradictions}
        self.assertIn("test_verify", culprit_nodes)

    def test_contradiction_flags_mixed_support(self):
        # one success + one failure is a contradiction, but there is still a
        # single successful experience supporting a (weak) conclusion.
        experiences = [
            _Exp("test_verify", "success_verified", "ctx_a", "xp_ok"),
            _Exp("test_verify", "failure", "ctx_a", "xp_fail"),
        ]
        out = reason(self.query, self.context, knowledge_nodes=[],
                     experiences=experiences)
        self.assertGreaterEqual(len(out.contradictions), 1)
        # failure never lends positive support, so only 1 support step (the
        # success) is in the chain.
        if out.conclusions:
            claims = set(s.source_id for s in
                         out.conclusions[0].evidence_chain.steps)
            self.assertIn("xp_ok", claims)
            self.assertNotIn("xp_fail", claims)

    def test_no_contradiction_when_all_success(self):
        experiences = [
            _Exp("test_verify", "success_verified", "ctx_a", "xp_ok1"),
            _Exp("test_verify", "success_verified", "ctx_a", "xp_ok2"),
        ]
        out = reason(self.query, self.context, knowledge_nodes=[],
                     experiences=experiences)
        self.assertEqual(
            [c.contradiction_type for c in out.contradictions], [])


if __name__ == "__main__":
    unittest.main()
