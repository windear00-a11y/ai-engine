"""Phase 7: decision rationale is complete and traceable (D8)."""

import unittest

from intelligence.decision import decide, DecisionStore
from intelligence.decision.audit import (
    get_decision, decisions_for_task, decision_audit_summary)
from intelligence.reasoning.evidence_chain import build_chain
from intelligence.reasoning.types import Conclusion, EvidenceStep, ReasoningQuery
from intelligence.reasoning.schema import ReasoningOutput


def _ro(chain=None, ctx_id="ctx_a", conf=0.81):
    if chain is None:
        chain = build_chain([EvidenceStep("k1", "knowledge", "x", 0.9),
                             EvidenceStep("xp1", "experience", "x", 0.85)])
    c = Conclusion(claim="bug_fix appropriate", confidence=conf,
                   evidence_chain=chain, context_id=ctx_id, source="knowledge")
    q = ReasoningQuery(question="q", task_type="bug_fix", domain="lint",
                       target={})
    return ReasoningOutput(reasoning_id="rs_a", query=q.to_dict(),
                           context_id=ctx_id, conclusions=[c],
                           contradictions=[], insufficient_evidence=[],
                           created_at_epoch=1.0)


CANDIDATES = [
    {"strategy_id": "st_a", "name": "bug_fix", "problem_class": "bug_fix",
     "tool_sequence": ["file.read", "file.diff"], "confidence": 0.8},
]


class DecisionAuditTrailTests(unittest.TestCase):
    def test_rationale_contains_evidence_chain(self):
        ro = _ro()
        store = DecisionStore(":memory:")
        self.addCleanup(store.close)
        d = decide("t1", "bug_fix", list(CANDIDATES), reasoning_output=ro,
                   context={"context_id": "ctx_a"}, store=store,
                   created_at_epoch=1.0)
        self.assertIn("evidence_chain", d.rationale)
        self.assertIn("score", d.rationale)
        self.assertIn("reasoning_id", d.rationale)
        self.assertEqual(d.rationale["reasoning_id"], ro.reasoning_id)

    def test_alternatives_rejected_recorded(self):
        ro = _ro()
        cands = [
            {"strategy_id": "st_a", "name": "a", "problem_class": "bug_fix",
             "tool_sequence": ["file.read"], "confidence": 0.9},
            {"strategy_id": "st_b", "name": "b", "problem_class": "bug_fix",
             "tool_sequence": ["file.read"], "confidence": 0.6},
        ]
        store = DecisionStore(":memory:")
        self.addCleanup(store.close)
        d = decide("t1", "bug_fix", cands, reasoning_output=ro,
                   context={"context_id": "ctx_a"}, store=store,
                   created_at_epoch=1.0)
        self.assertEqual(len(d.alternatives_rejected), 1)
        self.assertEqual(d.alternatives_rejected[0]["strategy_id"], "st_b")

    def test_decision_persisted_and_retrievable(self):
        ro = _ro()
        store = DecisionStore(":memory:")
        self.addCleanup(store.close)
        d = decide("t1", "bug_fix", list(CANDIDATES), reasoning_output=ro,
                   context={"context_id": "ctx_a"}, store=store,
                   created_at_epoch=1.0)
        row = get_decision(d.decision_id, store=store)
        self.assertIsNotNone(row)
        self.assertEqual(row["decision_id"], d.decision_id)
        self.assertEqual(row["selected_strategy_id"], d.selected_strategy_id)

    def test_decisions_for_task(self):
        ro = _ro()
        # Second decision uses a distinct reasoning id so the decision_id
        # differs (decision is deterministic on reasoning_id; same reasoning
        # at a different epoch is the same decision and is idempotently
        # deduplicated by the append-only store).
        ro2 = _ro(chain=ro.conclusions[0].evidence_chain, ctx_id="ctx_a",
                  conf=0.82)
        ro2.reasoning_id = "rs_b"
        store = DecisionStore(":memory:")
        self.addCleanup(store.close)
        decide("t1", "bug_fix", list(CANDIDATES), reasoning_output=ro,
               context={"context_id": "ctx_a"}, store=store,
               created_at_epoch=1.0)
        decide("t1", "bug_fix", list(CANDIDATES), reasoning_output=ro2,
               context={"context_id": "ctx_a"}, store=store,
               created_at_epoch=2.0)
        rows = decisions_for_task("t1", store=store)
        self.assertEqual(len(rows), 2)

    def test_audit_summary(self):
        ro = _ro()
        store = DecisionStore(":memory:")
        self.addCleanup(store.close)
        decide("t1", "bug_fix", list(CANDIDATES), reasoning_output=ro,
               context={"context_id": "ctx_a"}, store=store,
               created_at_epoch=1.0)
        summary = decision_audit_summary(limit=10, store=store)
        self.assertGreaterEqual(len(summary), 1)
        self.assertIn("decision_id", summary[0])

    def test_append_only_update_rejected(self):
        ro = _ro()
        store = DecisionStore(":memory:")
        self.addCleanup(store.close)
        d = decide("t1", "bug_fix", list(CANDIDATES), reasoning_output=ro,
                   context={"context_id": "ctx_a"}, store=store,
                   created_at_epoch=1.0)
        with self.assertRaises(Exception):
            store.conn.execute("UPDATE decisions SET context_id='x'")
        with self.assertRaises(Exception):
            store.conn.execute("DELETE FROM decisions")
