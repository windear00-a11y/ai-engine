"""Phase 6: experience patterns correctly inform conclusions."""

import unittest

from intelligence.reasoning import reason, ReasoningQuery

_CTX = {"context_id": "ctx_a"}


class _Exp:
    def __init__(self, task_type, outcome, context_id, cm=0.95, eid=None):
        self.task_type = task_type
        self.domain = "lint"
        self.context_id = context_id
        self.summary = {"outcome": outcome, "context_match": cm}
        self.experience_id = eid or ("xp_" + outcome)


class ReasoningExperienceAggregationTests(unittest.TestCase):
    def _query(self, task_type="bug_fix"):
        return ReasoningQuery(question="q", task_type=task_type,
                              domain="lint", target={})

    def test_successful_experience_supports_conclusion(self):
        exps = [_Exp("bug_fix", "success_verified", "ctx_a", cm=0.95)]
        out = reason(self._query(), _CTX, experiences=exps)
        self.assertEqual(len(out.conclusions), 1)
        c = out.conclusions[0]
        self.assertEqual(c.source, "experience")
        self.assertIn("100%", c.claim)

    def test_only_failure_no_positive_conclusion(self):
        exps = [_Exp("bug_fix", "failure", "ctx_a", cm=0.95)]
        out = reason(self._query(), _CTX, experiences=exps)
        self.assertEqual(out.conclusions, [])
        self.assertEqual(len(out.insufficient_evidence), 1)

    def test_wrong_task_type_excluded(self):
        exps = [_Exp("test_verify", "success_verified", "ctx_a", cm=0.95)]
        out = reason(self._query(), _CTX, experiences=exps)
        self.assertEqual(out.conclusions, [])

    def test_more_successes_raise_confidence(self):
        one = [_Exp("bug_fix", "success_verified", "ctx_a", cm=0.95, eid="a")]
        many = [_Exp("bug_fix", "success_verified", "ctx_a", cm=0.95, eid="a"),
                _Exp("bug_fix", "success_verified", "ctx_a", cm=0.95, eid="b"),
                _Exp("bug_fix", "success_verified", "ctx_a", cm=0.95, eid="c")]
        c_one = reason(self._query(), _CTX, experiences=one,
                       knowledge_nodes=[]).conclusions[0].confidence
        c_many = reason(self._query(), _CTX, experiences=many,
                        knowledge_nodes=[]).conclusions[0].confidence
        self.assertGreater(c_many, c_one)


if __name__ == "__main__":
    unittest.main()
