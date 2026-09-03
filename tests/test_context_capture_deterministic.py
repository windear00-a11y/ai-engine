"""Phase 1: same environment -> same deterministic context_id.

Two captures of the same environment (same host, same project root, same task
metadata) must produce the identical context_id, regardless of capture time.
"""

import unittest

from intelligence.context import capture_context


class ContextCaptureDeterministicTests(unittest.TestCase):
    def test_same_environment_same_id(self):
        a = capture_context()
        b = capture_context()
        self.assertEqual(a.context_id, b.context_id)

    def test_same_task_metadata_same_id(self):
        md = {"type": "bug_fix", "domain": "lint", "error_pattern": "E302"}
        a = capture_context(task_metadata=md)
        b = capture_context(task_metadata=md)
        self.assertEqual(a.context_id, b.context_id)

    def test_ids_are_stable_format(self):
        ctx = capture_context()
        self.assertTrue(ctx.context_id.startswith("ctx_"))
        # 4 hex bytes prefix -> 8 chars after "ctx_" at minimum
        self.assertEqual(len(ctx.context_id), 4 + 32)


if __name__ == "__main__":
    unittest.main()
