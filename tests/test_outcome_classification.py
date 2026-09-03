"""Phase 2: outcome classification is correct and deterministic."""

import unittest

from intelligence.outcome.types import OutcomeClassification, classify_outcome


class OutcomeClassificationTests(unittest.TestCase):
    def test_success_variants(self):
        for status in ("success", "verified", "passed", "ok"):
            self.assertEqual(classify_outcome(status),
                             OutcomeClassification.SUCCESS, status)
        self.assertEqual(classify_outcome({"status": "verified"}),
                         OutcomeClassification.SUCCESS)
        self.assertEqual(classify_outcome({"ok": True}),
                         OutcomeClassification.SUCCESS)
        self.assertEqual(classify_outcome({"passed": True}),
                         OutcomeClassification.SUCCESS)

    def test_failure_variants(self):
        for status in ("failed", "error", "unverified"):
            self.assertEqual(classify_outcome(status),
                             OutcomeClassification.FAILURE, status)
        self.assertEqual(classify_outcome({"ok": False}),
                         OutcomeClassification.FAILURE)
        self.assertEqual(classify_outcome({"passed": False}),
                         OutcomeClassification.FAILURE)

    def test_partial_and_blocked(self):
        self.assertEqual(classify_outcome("partial"),
                         OutcomeClassification.PARTIAL)
        for status in ("blocked", "denied", "skipped"):
            self.assertEqual(classify_outcome(status),
                             OutcomeClassification.BLOCKED, status)

    def test_unknown_input(self):
        self.assertEqual(classify_outcome(42), OutcomeClassification.UNKNOWN)
        self.assertEqual(classify_outcome({}),
                         OutcomeClassification.UNKNOWN)
        self.assertEqual(classify_outcome("nonsense"),
                         OutcomeClassification.UNKNOWN)


if __name__ == "__main__":
    unittest.main()
