"""Phase 7: policy-violating candidates are eliminated."""

import unittest

from intelligence.decision import decide, DecisionStore, Policy, PolicyEngine
from intelligence.reasoning.evidence_chain import build_chain
from intelligence.reasoning.types import Conclusion, EvidenceStep, ReasoningQuery
from intelligence.reasoning.schema import ReasoningOutput


def _ro(ctx_id="ctx_a", conf=0.81):
    chain = build_chain([EvidenceStep("k1", "knowledge", "x", 0.9)])
    c = Conclusion(claim="x", confidence=conf, evidence_chain=chain,
                   context_id=ctx_id, source="knowledge")
    q = ReasoningQuery(question="q", task_type="bug_fix", domain="lint",
                       target={})
    return ReasoningOutput(reasoning_id="rs_a", query=q.to_dict(),
                           context_id=ctx_id, conclusions=[c],
                           contradictions=[], insufficient_evidence=[],
                           created_at_epoch=1.0)


class DecisionPolicyFilteringTests(unittest.TestCase):
    def test_blocked_candidate_eliminated(self):
        ro = _ro()
        # High-risk candidate is blocked by the default approve policy.
        candidates = [
            {"strategy_id": "st_safe", "name": "safe",
             "problem_class": "bug_fix",
             "tool_sequence": ["file.read"], "confidence": 0.6},
            {"strategy_id": "st_danger", "name": "danger",
             "problem_class": "bug_fix",
             "tool_sequence": ["file.delete", "file.remove"],
             "confidence": 0.9},
        ]
        ctx = {"context_id": "ctx_a"}
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context=ctx, store=DecisionStore(":memory:"),
                   created_at_epoch=1.0)
        # danger is high risk -> blocked, safe is selected
        self.assertEqual(d.selected_strategy_id, "st_safe")
        rejected_ids = {r["strategy_id"] for r in d.alternatives_rejected}
        self.assertIn("st_danger", rejected_ids)

    def test_custom_policy_blocks_by_task_type(self):
        ro = _ro()
        policy = Policy(policy_id="block_bug_fix", domain="decide",
                        when={"task_type": "bug_fix"},
                        effect={"blocked": True},
                        description="block all bug_fix")
        engine = PolicyEngine(policies=[policy])
        candidates = [
            {"strategy_id": "st_a", "name": "a", "problem_class": "bug_fix",
             "tool_sequence": ["file.read"], "confidence": 0.9},
        ]
        ctx = {"context_id": "ctx_a"}
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context=ctx, policy_engine=engine,
                   store=DecisionStore(":memory:"), created_at_epoch=1.0)
        self.assertIsNone(d.selected_strategy_id)
        self.assertEqual(d.status, "needs_guidance")

    def test_policy_violation_recorded_in_audit(self):
        ro = _ro()
        candidates = [
            {"strategy_id": "st_danger", "name": "danger",
             "problem_class": "bug_fix",
             "tool_sequence": ["file.delete"], "confidence": 0.9},
        ]
        ctx = {"context_id": "ctx_a"}
        store = DecisionStore(":memory:")
        self.addCleanup(store.close)
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context=ctx, store=store, created_at_epoch=1.0)
        row = store.get(d.decision_id)
        self.assertIsNotNone(row)
        self.assertIn("policy_violation",
                      str(row["alternatives_rejected"]))

    def test_context_restricted_strategy_filtered(self):
        ro = _ro()
        candidates = [
            {"strategy_id": "st_other", "name": "other",
             "problem_class": "bug_fix",
             "tool_sequence": ["file.read"], "confidence": 0.9,
             "context_restrictions": {"allowed_contexts": ["ctx_other"]}},
        ]
        ctx = {"context_id": "ctx_a"}
        d = decide("t1", "bug_fix", candidates, reasoning_output=ro,
                   context=ctx, store=DecisionStore(":memory:"),
                   created_at_epoch=1.0)
        self.assertIsNone(d.selected_strategy_id)
        self.assertEqual(d.status, "needs_guidance")
