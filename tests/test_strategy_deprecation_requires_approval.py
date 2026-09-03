"""Phase 4: deprecation is flagged, not auto-applied, until approved."""

import unittest

from intelligence.strategy import (
    approve_deprecation,
    get_strategy,
    propose_deprecation,
    register_strategy,
)
from intelligence.strategy import derive_strategy_id
from intelligence.strategy.store import StrategyStore


class StrategyDeprecationTests(unittest.TestCase):
    def setUp(self):
        self.store = StrategyStore(":memory:")
        self.addCleanup(self.store.close)
        self.sid = derive_strategy_id("bug_fix", "bug_fix")
        register_strategy(self.sid, "bug_fix", "d", "bug_fix", ["a"],
                          store=self.store, created_at_epoch=1.0)

    def test_propose_does_not_apply(self):
        result = propose_deprecation(self.sid, store=self.store,
                                     created_at_epoch=2.0)
        self.assertFalse(result.deprecated)
        stored = get_strategy(self.sid, store=self.store)
        self.assertFalse(stored.deprecated)

    def test_propose_records_pending_in_audit(self):
        propose_deprecation(self.sid, store=self.store, created_at_epoch=2.0)
        trail = self.store.audit_trail(self.sid)
        self.assertEqual(len(trail), 1)
        self.assertEqual(trail[0]["field"], "deprecated")
        self.assertIn("pending", trail[0]["note"])

    def test_approve_applies(self):
        approved = approve_deprecation(self.sid, store=self.store,
                                       created_at_epoch=3.0)
        self.assertTrue(approved.deprecated)
        stored = get_strategy(self.sid, store=self.store)
        self.assertTrue(stored.deprecated)

    def test_deprecated_strategy_still_retrievable(self):
        approve_deprecation(self.sid, store=self.store, created_at_epoch=3.0)
        got = get_strategy(self.sid, store=self.store)
        self.assertEqual(got.name, "bug_fix")
        self.assertTrue(got.deprecated)

    def test_approve_without_propose_still_applies(self):
        approved = approve_deprecation(self.sid, store=self.store,
                                       created_at_epoch=3.0)
        self.assertTrue(approved.deprecated)


if __name__ == "__main__":
    unittest.main()
