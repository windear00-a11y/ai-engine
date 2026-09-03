"""Phase 4: confidence changes are recorded with evidence."""

import json
import unittest

from intelligence.strategy import (
    get_strategy,
    register_strategy,
    update_strategy_confidence,
)
from intelligence.strategy import derive_strategy_id
from intelligence.strategy.store import StrategyStore


class StrategyConfidenceUpdateTests(unittest.TestCase):
    def setUp(self):
        self.store = StrategyStore(":memory:")
        self.addCleanup(self.store.close)
        self.sid = derive_strategy_id("bug_fix", "bug_fix")
        register_strategy(self.sid, "bug_fix", "d", "bug_fix", ["a"],
                          confidence=0.4, store=self.store,
                          created_at_epoch=1.0)

    def test_delta_applied(self):
        updated = update_strategy_confidence(
            self.sid, 0.2, ["ev_1"], store=self.store, created_at_epoch=2.0)
        self.assertAlmostEqual(updated.confidence, 0.6)

    def test_clamped_to_bounds(self):
        up = update_strategy_confidence(
            self.sid, 10.0, ["ev_1"], store=self.store, created_at_epoch=2.0)
        self.assertAlmostEqual(up.confidence, 1.0)
        down = update_strategy_confidence(
            self.sid, -10.0, ["ev_1"], store=self.store, created_at_epoch=3.0)
        self.assertAlmostEqual(down.confidence, 0.0)

    def test_change_audited_with_evidence(self):
        update_strategy_confidence(
            self.sid, 0.1, ["ev_1", "ev_2"], store=self.store,
            created_at_epoch=2.0)
        trail = self.store.audit_trail(self.sid)
        self.assertEqual(len(trail), 1)
        audit = trail[0]
        self.assertEqual(audit["field"], "confidence")
        self.assertEqual(sorted(json.loads(audit["evidence_ids_json"])),
                         ["ev_1", "ev_2"])

    def test_audit_is_append_only(self):
        update_strategy_confidence(
            self.sid, 0.1, ["a"], store=self.store, created_at_epoch=2.0)
        update_strategy_confidence(
            self.sid, 0.1, ["b"], store=self.store, created_at_epoch=3.0)
        self.assertEqual(len(self.store.audit_trail(self.sid)), 2)

    def test_missing_strategy_raises(self):
        with self.assertRaises(KeyError):
            update_strategy_confidence(
                derive_strategy_id("nope", "nope"), 0.1, ["a"],
                store=self.store)

    def test_audit_trail_is_append_only(self):
        update_strategy_confidence(
            self.sid, 0.1, ["ev_1"], store=self.store, created_at_epoch=2.0)
        self.assertEqual(len(self.store.audit_trail(self.sid)), 1)
        with self.assertRaises(Exception):
            self.store.conn.execute("UPDATE strategy_audit SET note='x'")
        with self.assertRaises(Exception):
            self.store.conn.execute("DELETE FROM strategy_audit")
        self.assertEqual(len(self.store.audit_trail(self.sid)), 1)


if __name__ == "__main__":
    unittest.main()
