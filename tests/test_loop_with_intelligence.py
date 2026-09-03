"""Phase 9: full loop with all components."""

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


TASK = {"task_id": "t_full", "task_type": "bug_fix", "domain": "lint",
        "target": {"file": "src/utils.py", "error": "E302"},
        "description": "Fix E302"}
KNOWLEDGE = [{"id": "k1", "type": "fact", "name": "E302 fixed by inserting blank line",
              "description": "E302 fixed", "subject": "e302",
              "lifecycle": {"confidence": 0.85}}]


class LoopWithIntelligenceTests(unittest.TestCase):
    def test_full_loop_produces_all_ids(self):
        stores = {
            "strategy_store": StrategyStore(":memory:"),
            "outcome_store": OutcomeStore(":memory:"),
            "experience_store": ExperienceStore(":memory:"),
            "learning_store": LearningStore(":memory:"),
            "evidence_store": EvidenceStore(":memory:"),
            "reasoning_store": ReasoningStore(":memory:"),
            "decision_store": DecisionStore(":memory:"),
            "context_store": ContextStore(":memory:"),
        }
        initialize_strategies_from_planner(store=stores["strategy_store"], created_at_epoch=1.0)
        res = execute_intelligence_loop(TASK, workspace_root="/tmp",
                                        knowledge_nodes=KNOWLEDGE, **stores)
        self.assertIsNotNone(res.context_id)
        self.assertIsNotNone(res.reasoning_id)
        self.assertIsNotNone(res.decision_id)
        self.assertIsNotNone(res.plan_id)
        self.assertIsNotNone(res.outcome_id)
        self.assertIsNotNone(res.experience_id)
        self.assertIsNotNone(res.learning_event_id)
        self.assertTrue(res.ok)
        self.assertFalse(res.fallback_used)
        for v in stores.values():
            v.close()

    def test_loop_degrades_but_still_ok_with_no_knowledge(self):
        stores = {
            "strategy_store": StrategyStore(":memory:"),
            "outcome_store": OutcomeStore(":memory:"),
            "experience_store": ExperienceStore(":memory:"),
            "learning_store": LearningStore(":memory:"),
            "evidence_store": EvidenceStore(":memory:"),
            "reasoning_store": ReasoningStore(":memory:"),
            "decision_store": DecisionStore(":memory:"),
            "context_store": ContextStore(":memory:"),
        }
        initialize_strategies_from_planner(store=stores["strategy_store"], created_at_epoch=1.0)
        # No knowledge supplied; reasoning will have insufficient evidence but loop still completes
        res = execute_intelligence_loop(TASK, workspace_root="/tmp",
                                        knowledge_nodes=[], **stores)
        self.assertTrue(res.ok)
        self.assertIsNotNone(res.outcome_id)
        for v in stores.values():
            v.close()
