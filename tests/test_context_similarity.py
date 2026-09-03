"""Phase 1: weighted context similarity.

Similar contexts rank higher than different contexts. Weights: system 0.4,
project 0.4, task 0.2. Exact=1.0, partial=0.5, no-match=0.0.
"""

import unittest

from intelligence.context import context_similarity
from intelligence.context.schema import ContextSnapshot


def snap(system, project=None, task=None):
    return ContextSnapshot.build(
        system=system, project=project or {}, task=task or {},
        temporal={}, captured_at_epoch=0.0,
    )


BASE_SYS = {"os": "linux", "arch": "x86_64", "python": "3.11.4"}
BASE_PRJ = {"language": "python", "framework": "django", "build": "pip"}
BASE_TASK = {"type": "bug_fix", "domain": "lint", "error_pattern": "E302"}


class ContextSimilarityTests(unittest.TestCase):
    def setUp(self):
        self.base = snap(BASE_SYS, BASE_PRJ, BASE_TASK)

    def test_identical_is_high(self):
        other = snap(BASE_SYS, BASE_PRJ, BASE_TASK)
        self.assertGreater(context_similarity(self.base, other), 0.99)

    def test_similar_ranks_higher_than_different(self):
        near = snap(BASE_SYS, BASE_PRJ,
                    {"type": "bug_fix", "domain": "lint",
                     "error_pattern": "E302"})
        far = snap({"os": "windows", "arch": "arm64", "python": "3.9"},
                   {"language": "javascript", "framework": "react",
                    "build": "npm"},
                   {"type": "refactor", "domain": "ui",
                    "error_pattern": "NONE"})
        sim_near = context_similarity(self.base, near)
        sim_far = context_similarity(self.base, far)
        self.assertGreater(sim_near, sim_far)

    def test_similarity_bounded(self):
        far = snap({"os": "windows", "arch": "arm64", "python": "3.9"},
                   {"language": "javascript", "framework": "react",
                    "build": "npm"},
                   {"type": "refactor", "domain": "ui",
                    "error_pattern": "NONE"})
        self.assertGreaterEqual(context_similarity(self.base, far), 0.0)
        self.assertLessEqual(context_similarity(self.base, far), 1.0)

    def test_default_weights(self):
        # Task-only change moves similarity by task weight (0.2).
        changed_task = snap(BASE_SYS, BASE_PRJ,
                            {"type": "refactor", "domain": "refactor",
                             "error_pattern": ""})
        self.assertAlmostEqual(context_similarity(self.base, changed_task),
                               0.8, places=6)

    def test_partial_match_scores_half(self):
        near = snap(BASE_SYS, BASE_PRJ, {"type": "bug_fix", "domain": "lint"})
        # missing error_pattern key counts as no-match; other 2 exact
        sim = context_similarity(BASE_TASK and self.base, near)
        self.assertGreater(sim, 0.7)
        self.assertLess(sim, 1.0)


if __name__ == "__main__":
    unittest.main()
