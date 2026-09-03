"""Phase 4: existing planner rules become initial strategies."""

import unittest

from intelligence.strategy import (
    get_strategy,
    initialize_strategies_from_planner,
)
from intelligence.strategy import derive_strategy_id
from intelligence.strategy.registry import _PLANNER_SEEDS
from intelligence.strategy.store import StrategyStore


class InitializeFromPlannerTests(unittest.TestCase):
    def setUp(self):
        self.store = StrategyStore(":memory:")
        self.addCleanup(self.store.close)

    def test_seeds_all_planner_plan_types(self):
        strategies = initialize_strategies_from_planner(store=self.store)
        names = {s.name for s in strategies}
        self.assertEqual(names, {"bug_fix", "test_verify", "generic"})
        # every seed in the planner template table is emitted
        self.assertEqual(len(strategies), len(_PLANNER_SEEDS))

    def test_seeded_strategy_has_tool_sequence(self):
        strategies = initialize_strategies_from_planner(store=self.store)
        bug = next(s for s in strategies if s.problem_class == "bug_fix")
        self.assertTrue(len(bug.tool_sequence) >= 4)
        self.assertEqual(bug.tool_sequence[0], "file.read")

    def test_seeding_is_idempotent(self):
        initialize_strategies_from_planner(store=self.store)
        initialize_strategies_from_planner(store=self.store)
        self.assertEqual(len(self.store.all()), len(_PLANNER_SEEDS))

    def test_seeded_strategy_retrievable(self):
        initialize_strategies_from_planner(store=self.store)
        sid = derive_strategy_id("generic", "generic")
        got = get_strategy(sid, store=self.store)
        self.assertEqual(got.name, "generic")

    def test_seeds_are_explicit_type(self):
        strategies = initialize_strategies_from_planner(store=self.store)
        for s in strategies:
            self.assertEqual(s.strategy_type, "explicit")


if __name__ == "__main__":
    unittest.main()
