"""Phase 4: patterns in experience produce strategy candidates.

Recognition is deterministic: identical experience records always yield
identical candidates.
"""

import unittest

from intelligence.strategy import recognize_strategies_from_experience


class _Exp:
    def __init__(self, task_type, outcome, experience_id, evidence_ids=()):
        self.task_type = task_type
        self.summary = {"outcome": outcome}
        self.experience_id = experience_id
        self.evidence_ids = list(evidence_ids)


class StrategyRecognitionTests(unittest.TestCase):
    def test_below_min_samples_produces_no_candidate(self):
        records = [_Exp("bug_fix", "success", "a"),
                   _Exp("bug_fix", "success", "b")]
        candidates = recognize_strategies_from_experience(records, min_samples=3)
        self.assertEqual(candidates, [])

    def test_sample_count_and_success_rate(self):
        records = [
            _Exp("bug_fix", "success", "e1"),
            _Exp("bug_fix", "success", "e2"),
            _Exp("bug_fix", "failure", "e3"),
            _Exp("bug_fix", "success", "e4"),
        ]
        candidates = recognize_strategies_from_experience(records, min_samples=3)
        self.assertEqual(len(candidates), 1)
        cand = candidates[0]
        self.assertEqual(cand.problem_class, "bug_fix")
        self.assertEqual(cand.sample_count, 4)
        self.assertEqual(cand.success_rate, 0.75)

    def test_confidence_deterministic_and_bounded(self):
        records = [
            _Exp("bug_fix", "success", "e1"),
            _Exp("bug_fix", "success", "e2"),
            _Exp("bug_fix", "success", "e3"),
        ]
        c1 = recognize_strategies_from_experience(records, min_samples=3)
        c2 = recognize_strategies_from_experience(records, min_samples=3)
        self.assertEqual(c1[0].confidence, c2[0].confidence)
        self.assertTrue(0.0 <= c1[0].confidence <= 1.0)

    def test_evidence_ids_aggregated_deduped(self):
        records = [
            _Exp("bug_fix", "success", "e1", ["ev_x", "ev_y"]),
            _Exp("bug_fix", "success", "e2", ["ev_x"]),
            _Exp("bug_fix", "success", "e3", ["ev_z"]),
        ]
        candidates = recognize_strategies_from_experience(records, min_samples=3)
        self.assertEqual(sorted(candidates[0].evidence_ids),
                         ["ev_x", "ev_y", "ev_z"])

    def test_multiple_problem_classes_ordered(self):
        def group(n, tt):
            return [_Exp(tt, "success", f"{tt}-{i}") for i in range(n)]
        records = group(5, "generic") + group(3, "bug_fix")
        candidates = recognize_strategies_from_experience(records, min_samples=3)
        classes = [c.problem_class for c in candidates]
        # generic (5 samples) ranks above bug_fix (3 samples) at equal success
        self.assertEqual(classes, ["generic", "bug_fix"])

    def test_non_success_outcome_recognized(self):
        records = [_Exp("refactor", "failure", "a"),
                   _Exp("refactor", "failure", "b"),
                   _Exp("refactor", "failure", "c")]
        candidates = recognize_strategies_from_experience(records, min_samples=3)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].success_rate, 0.0)


if __name__ == "__main__":
    unittest.main()
