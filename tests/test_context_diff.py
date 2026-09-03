"""Phase 1: context_diff correctly identifies changed dimensions."""

import unittest

from intelligence.context import context_diff
from intelligence.context.schema import ContextSnapshot


def snap(system, project=None, task=None):
    return ContextSnapshot.build(
        system=system, project=project or {}, task=task or {},
        temporal={}, captured_at_epoch=0.0,
    )


BASE_SYS = {"os": "linux", "arch": "x86_64", "python": "3.11.4"}
BASE_PRJ = {"language": "python", "framework": "django", "build": "pip"}


class ContextDiffTests(unittest.TestCase):
    def test_no_changes_when_identical(self):
        a = snap(BASE_SYS, BASE_PRJ, {"type": "bug_fix"})
        d = context_diff(a, snap(BASE_SYS, BASE_PRJ, {"type": "bug_fix"}))
        self.assertEqual(d["changed_dimensions"], [])
        self.assertGreaterEqual(d["similarity"], 1.0)

    def test_detects_task_change(self):
        a = snap(BASE_SYS, BASE_PRJ, {"type": "bug_fix"})
        b = snap(BASE_SYS, BASE_PRJ, {"type": "refactor"})
        d = context_diff(a, b)
        self.assertIn("task", d["changed_dimensions"])
        self.assertTrue(d["dimensions"]["task"]["changed"])
        self.assertNotIn("system", d["changed_dimensions"])

    def test_detects_project_change(self):
        a = snap(BASE_SYS, BASE_PRJ, {"type": "bug_fix"})
        b = snap(BASE_SYS, {"language": "javascript", "framework": "react",
                            "build": "npm"}, {"type": "bug_fix"})
        d = context_diff(a, b)
        self.assertIn("project", d["changed_dimensions"])
        self.assertTrue(d["dimensions"]["project"]["changed"])

    def test_reports_changed_keys(self):
        a = snap(BASE_SYS, {"language": "python"}, {"type": "bug_fix"})
        b = snap(BASE_SYS, {"language": "rust"}, {"type": "bug_fix"})
        d = context_diff(a, b)
        self.assertIn("language", d["dimensions"]["project"]["changed_keys"])


if __name__ == "__main__":
    unittest.main()
