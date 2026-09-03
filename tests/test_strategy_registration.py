"""Phase 4: strategies can be registered and retrieved."""

import unittest

from intelligence.strategy import (
    get_strategy,
    register_strategy,
    strategies_for_problem_class,
)
from intelligence.strategy import derive_strategy_id
from intelligence.strategy.store import StrategyStore


class StrategyRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.store = StrategyStore(":memory:")
        self.addCleanup(self.store.close)

    def test_register_and_get(self):
        sid = derive_strategy_id("bug_fix", "bug_fix")
        register_strategy(
            sid, "bug_fix", "fix a defect", "bug_fix",
            ["file.read", "code.analyze", "project.test"],
            constraints={"max_steps": 7},
            store=self.store, created_at_epoch=1.0)
        got = get_strategy(sid, store=self.store)
        self.assertEqual(got.name, "bug_fix")
        self.assertEqual(got.problem_class, "bug_fix")
        self.assertEqual(list(got.tool_sequence),
                         ["file.read", "code.analyze", "project.test"])
        self.assertEqual(got.constraints, {"max_steps": 7})
        self.assertEqual(got.created_at_epoch, 1.0)
        self.assertFalse(got.deprecated)

    def test_strategies_for_problem_class(self):
        for name in ("s1", "s2", "s3"):
            sid = derive_strategy_id("bug_fix", name)
            register_strategy(sid, name, "desc", "bug_fix",
                              ["a"], store=self.store, confidence=0.5)
        for name in ("t1",):
            sid = derive_strategy_id("test_verify", name)
            register_strategy(sid, name, "desc", "test_verify",
                              ["b"], store=self.store, confidence=0.9)
        bug = strategies_for_problem_class("bug_fix", store=self.store)
        tv = strategies_for_problem_class("test_verify", store=self.store)
        self.assertEqual({s.name for s in bug}, {"s1", "s2", "s3"})
        self.assertEqual([s.name for s in tv], ["t1"])
        # ordered by confidence desc
        self.assertEqual([s.confidence for s in bug], [0.5, 0.5, 0.5])

    def test_registration_additive_idempotent(self):
        sid = derive_strategy_id("bug_fix", "bug_fix")
        register_strategy(sid, "bug_fix", "v1", "bug_fix", ["a"],
                          store=self.store)
        register_strategy(sid, "bug_fix", "v2-changed", "bug_fix", ["b"],
                          store=self.store)
        got = get_strategy(sid, store=self.store)
        # idempotent: first registration wins, nothing overwritten
        self.assertEqual(got.description, "v1")
        self.assertEqual(list(got.tool_sequence), ["a"])

    def test_get_missing_raises(self):
        with self.assertRaises(KeyError):
            get_strategy(derive_strategy_id("nope", "nope"), store=self.store)


if __name__ == "__main__":
    unittest.main()
