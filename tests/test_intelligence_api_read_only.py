"""Phase 10: intelligence API is read-only, no mutations possible."""

import unittest

from intelligence.api import IntelligenceToolInterface
from intelligence.experience.store import ExperienceStore
from intelligence.decision.store import DecisionStore
from intelligence.learning.store import LearningStore
from intelligence.context.store import ContextStore


class IntelligenceAPIReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.api = IntelligenceToolInterface(
            experience_store=ExperienceStore(":memory:"),
            decision_store=DecisionStore(":memory:"),
            learning_store=LearningStore(":memory:"),
            context_store=ContextStore(":memory:"),
        )

    def test_unknown_mutating_operations_rejected(self):
        for op in ["experience.create", "decision.create", "learning.create",
                   "knowledge.update", "context.delete", "experience.delete"]:
            res = self.api.execute({"operation": op, "arguments": {}})
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"]["code"], "unknown_operation")

    def test_no_write_arguments_accepted(self):
        # Even valid operations reject extra keys that could smuggle writes
        res = self.api.execute({"operation": "experience.search",
                                "arguments": {"task_type": "bug_fix", "sql": "DROP"}})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "invalid_argument")

    def test_valid_read_does_not_mutate(self):
        exp_store = ExperienceStore(":memory:")
        api = IntelligenceToolInterface(experience_store=exp_store,
                                        decision_store=DecisionStore(":memory:"),
                                        learning_store=LearningStore(":memory:"),
                                        context_store=ContextStore(":memory:"))
        before = exp_store.count()
        api.execute({"operation": "experience.search", "arguments": {"limit": 5}})
        after = exp_store.count()
        self.assertEqual(before, after)

    def test_all_operations_are_read_only(self):
        # Ensure contract has no mutating operations
        from intelligence.api.contract import OPERATIONS
        for op in OPERATIONS:
            self.assertNotIn("create", op)
            self.assertNotIn("delete", op)
            self.assertNotIn("update", op)
