"""Phase 9: loop is deterministic (same inputs -> same result)."""

import unittest

from intelligence.loop import execute_intelligence_loop
from intelligence.strategy.store import StrategyStore
from intelligence.strategy.registry import initialize_strategies_from_planner
from intelligence.outcome.store import OutcomeStore
from intelligence.experience.store import ExperienceStore
from intelligence.learning.store import LearningStore
from intelligence.evidence.store import EvidenceStore
from intelligence.reasoning.store import ReasoningStore
from intelligence.decision.store import DecisionStore
from intelligence.context.store import ContextStore


def _fresh_stores():
    s = StrategyStore(":memory:")
    initialize_strategies_from_planner(store=s, created_at_epoch=1.0)
    return {
        "strategy_store": s,
        "outcome_store": OutcomeStore(":memory:"),
        "experience_store": ExperienceStore(":memory:"),
        "learning_store": LearningStore(":memory:"),
        "evidence_store": EvidenceStore(":memory:"),
        "reasoning_store": ReasoningStore(":memory:"),
        "decision_store": DecisionStore(":memory:"),
        "context_store": ContextStore(":memory:"),
    }


TASK = {"task_id": "t_det", "task_type": "bug_fix", "domain": "lint",
        "target": {"file": "src/utils.py", "error": "E302"},
        "description": "Fix E302"}
KNOWLEDGE = [{"id": "k1", "type": "fact", "name": "E302 fixed by inserting blank line",
              "description": "E302 fixed", "subject": "e302",
              "lifecycle": {"confidence": 0.85}}]


class LoopDeterministicTests(unittest.TestCase):
    def _run_fresh(self):
        stores = _fresh_stores()
        res = execute_intelligence_loop(TASK, workspace_root="/tmp",
                                        knowledge_nodes=KNOWLEDGE, **stores)
        for v in stores.values():
            v.close()
        return res

    def test_same_inputs_same_result(self):
        a = self._run_fresh()
        b = self._run_fresh()
        self.assertEqual(a.reasoning_id, b.reasoning_id)
        self.assertEqual(a.decision_id, b.decision_id)
        self.assertEqual(a.plan_id, b.plan_id)
        # context_id deterministic for same env
        self.assertEqual(a.context_id, b.context_id)

    def test_result_serializable(self):
        res = self._run_fresh()
        d = res.to_dict()
        self.assertIn("task_id", d)
        self.assertIn("reasoning_id", d)
        self.assertIn("decision_id", d)
        self.assertIn("ok", d)
