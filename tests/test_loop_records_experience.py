"""Phase 9: execution produces an experience record."""

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


TASK = {"task_id": "t_exp", "task_type": "bug_fix", "domain": "lint",
        "target": {"file": "src/utils.py", "error": "E302"},
        "description": "Fix E302"}
KNOWLEDGE = [{"id": "k1", "type": "fact", "name": "E302 fixed by inserting blank line",
              "description": "E302 fixed", "subject": "e302",
              "lifecycle": {"confidence": 0.85}}]


class LoopRecordsExperienceTests(unittest.TestCase):
    def test_experience_recorded(self):
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
        self.assertIsNotNone(res.experience_id)
        exps = stores["experience_store"].all()
        self.assertEqual(len(exps), 1)
        self.assertEqual(exps[0].experience_id, res.experience_id)
        self.assertEqual(exps[0].task_id, TASK["task_id"])
        self.assertEqual(exps[0].outcome_id, res.outcome_id)
        for v in stores.values():
            v.close()

    def test_experience_links_to_outcome_and_evidence(self):
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
        exp = stores["experience_store"].get(res.experience_id)
        self.assertIsNotNone(exp)
        self.assertIsNotNone(exp.outcome_id)
        self.assertGreater(len(exp.evidence_ids), 0)
        for v in stores.values():
            v.close()
