"""Phase 6: reasoning outputs are persisted in evidence.db (append-only)."""

import unittest

from intelligence.reasoning import reason, ReasoningQuery
from intelligence.reasoning.store import ReasoningStore


class _Exp:
    def __init__(self, task_type, context_id, eid):
        self.task_type = task_type
        self.domain = "lint"
        self.context_id = context_id
        self.summary = {"outcome": "success_verified", "context_match": 0.95}
        self.experience_id = eid


class ReasoningOutputsImmutableTests(unittest.TestCase):
    def setUp(self):
        self.store = ReasoningStore(":memory:")
        self.addCleanup(self.store.close)
        self.query = ReasoningQuery(question="q", task_type="bug_fix",
                                    domain="lint", target={})
        self.context = {"context_id": "ctx_a"}

    def test_output_persisted_and_retrievable(self):
        exps = [_Exp("bug_fix", "ctx_a", "xp1")]
        out = reason(self.query, self.context, experiences=exps,
                     store=self.store, created_at_epoch=1.0)
        row = self.store.get(out.reasoning_id)
        self.assertIsNotNone(row)
        self.assertEqual(row["reasoning_id"], out.reasoning_id)
        self.assertEqual(row["context_id"], "ctx_a")

    def test_append_only_update_rejected(self):
        exps = [_Exp("bug_fix", "ctx_a", "xp1")]
        reason(self.query, self.context, experiences=exps,
               store=self.store, created_at_epoch=1.0)
        with self.assertRaises(Exception):
            self.store.conn.execute(
                "UPDATE reasoning_outputs SET context_id='x'")
        with self.assertRaises(Exception):
            self.store.conn.execute("DELETE FROM reasoning_outputs")

    def test_idempotent_save(self):
        exps = [_Exp("bug_fix", "ctx_a", "xp1")]
        a = reason(self.query, self.context, experiences=exps,
                   store=self.store, created_at_epoch=1.0)
        b = reason(self.query, self.context, experiences=exps,
                   store=self.store, created_at_epoch=1.0)
        self.assertEqual(a.reasoning_id, b.reasoning_id)
        self.assertEqual(len(self.store.all()), 1)


if __name__ == "__main__":
    unittest.main()
