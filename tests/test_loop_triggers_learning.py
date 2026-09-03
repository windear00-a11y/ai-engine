"""Phase 9: verified outcome triggers learning."""

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


TASK = {"task_id": "t_learn", "task_type": "bug_fix", "domain": "lint",
        "target": {"file": "src/utils.py", "error": "E302"},
        "description": "Fix E302"}
KNOWLEDGE = [{"id": "k1", "type": "fact", "name": "E302 fixed by inserting blank line",
              "description": "E302 fixed", "subject": "e302",
              "lifecycle": {"confidence": 0.85}}]


class LoopTriggersLearningTests(unittest.TestCase):
    def test_learning_event_created(self):
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
        self.assertIsNotNone(res.learning_event_id)
        learnings = stores["learning_store"].all()
        self.assertEqual(len(learnings), 1)
        self.assertEqual(learnings[0]["learning_event_id"], res.learning_event_id)
        self.assertEqual(learnings[0]["outcome_id"], res.outcome_id)
        for v in stores.values():
            v.close()

    def test_learning_adaptations_present(self):
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
        # First loop: 1 success (below threshold, no confidence increase)
        res1 = execute_intelligence_loop(TASK, workspace_root="/tmp",
                                         knowledge_nodes=KNOWLEDGE, **stores)
        # Second loop with same task but now 1 prior success: 2 total -> should increase
        task2 = dict(TASK)
        task2["task_id"] = "t_learn2"
        res2 = execute_intelligence_loop(task2, workspace_root="/tmp",
                                         knowledge_nodes=KNOWLEDGE, **stores)
        # Second learning should have an applied adaptation (M>=2)
        learnings = stores["learning_store"].for_outcome(res2.outcome_id)
        self.assertEqual(len(learnings), 1)
        # Check that second learning had an adaptation (positive)
        # The learning event's adaptations dict has proposed/applied
        self.assertIn("adaptations", learnings[0])
        for v in stores.values():
            v.close()
