"""Phase 4: strategies can be context-restricted."""

import unittest

from intelligence.strategy import (
    get_strategy,
    register_strategy,
    strategies_for_problem_class,
)
from intelligence.strategy import derive_strategy_id
from intelligence.strategy.store import StrategyStore


class StrategyContextRestrictionTests(unittest.TestCase):
    def setUp(self):
        self.store = StrategyStore(":memory:")
        self.addCleanup(self.store.close)

    def test_context_restrictions_persisted(self):
        sid = derive_strategy_id("bug_fix", "rust_fix")
        register_strategy(
            sid, "rust_fix", "rust-specific bug fix", "bug_fix",
            ["file.read", "code.analyze", "project.test"],
            context_restrictions={"language": "rust"},
            store=self.store, created_at_epoch=1.0)
        got = get_strategy(sid, store=self.store)
        self.assertEqual(got.context_restrictions, {"language": "rust"})

    def test_empty_context_restrictions_default(self):
        sid = derive_strategy_id("generic", "generic")
        register_strategy(sid, "generic", "d", "generic", ["a"],
                          store=self.store)
        got = get_strategy(sid, store=self.store)
        self.assertEqual(got.context_restrictions, {})

    def test_restricted_and_unrestricted_coexist_by_problem_class(self):
        s_restricted = derive_strategy_id("bug_fix", "rust_fix")
        s_general = derive_strategy_id("bug_fix", "bug_fix")
        register_strategy(
            s_restricted, "rust_fix", "rust", "bug_fix", ["a"],
            context_restrictions={"language": "rust"},
            store=self.store, confidence=0.9)
        register_strategy(
            s_general, "bug_fix", "general", "bug_fix", ["b"],
            store=self.store, confidence=0.5)
        got = strategies_for_problem_class("bug_fix", store=self.store)
        self.assertEqual(len(got), 2)
        # order by confidence desc
        self.assertEqual([s.name for s in got], ["rust_fix", "bug_fix"])


if __name__ == "__main__":
    unittest.main()
