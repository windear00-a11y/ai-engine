"""Phase 9: mutating actions go through approval."""

import unittest

from intelligence.loop import execute_intelligence_loop
from intelligence.strategy.schema import Strategy
from intelligence.strategy.store import StrategyStore
from intelligence.outcome.store import OutcomeStore
from intelligence.experience.store import ExperienceStore
from intelligence.learning.store import LearningStore
from intelligence.evidence.store import EvidenceStore
from intelligence.reasoning.store import ReasoningStore
from intelligence.decision.store import DecisionStore
from intelligence.context.store import ContextStore


KNOWLEDGE = [{"id": "k1", "type": "fact", "name": "E302 fixed by inserting blank line",
              "description": "E302 fixed", "subject": "e302",
              "lifecycle": {"confidence": 0.90}}]


class LoopRespectsApprovalTests(unittest.TestCase):
    def _stores(self):
        s = StrategyStore(":memory:")
        # Add a mutating strategy that will be selected for bug_fix (high confidence)
        mut = Strategy(strategy_id="st_mut", name="mut", description="mut",
                       problem_class="bug_fix", tool_sequence=["file.write", "project.test"],
                       confidence=0.95, strategy_type="explicit",
                       created_at_epoch=1.0, updated_at_epoch=1.0)
        s.save(mut)
        # Also add a read-only fallback
        ro = Strategy(strategy_id="st_ro", name="ro", description="ro",
                      problem_class="bug_fix", tool_sequence=["file.read"],
                      confidence=0.5, strategy_type="explicit",
                      created_at_epoch=1.0, updated_at_epoch=1.0)
        s.save(ro)
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

    def test_mutating_without_approval_awaits(self):
        task = {"task_id": "t_approval", "task_type": "bug_fix", "domain": "lint",
                "target": {"file": "src/utils.py", "error": "E302"},
                "description": "Fix E302"}
        stores = self._stores()
        res = execute_intelligence_loop(task, workspace_root="/tmp",
                                        knowledge_nodes=KNOWLEDGE,
                                        operator_approval=lambda d: False, **stores)
        self.assertTrue(res.approval_required)
        self.assertEqual(res.status, "awaiting_approval")
        self.assertIsNone(res.outcome_id)
        self.assertIsNone(res.experience_id)
        for v in stores.values():
            v.close()

    def test_mutating_with_approval_executes(self):
        task = {"task_id": "t_approval2", "task_type": "bug_fix", "domain": "lint",
                "target": {"file": "src/utils.py", "error": "E302"},
                "description": "Fix E302"}
        stores = self._stores()
        res = execute_intelligence_loop(task, workspace_root="/tmp",
                                        knowledge_nodes=KNOWLEDGE,
                                        operator_approval=lambda d: True, **stores)
        self.assertTrue(res.ok)
        self.assertIsNotNone(res.outcome_id)
        self.assertIsNotNone(res.experience_id)
        for v in stores.values():
            v.close()

    def test_read_only_no_approval_needed(self):
        # Use only read-only strategies
        s = StrategyStore(":memory:")
        ro = Strategy(strategy_id="st_ro2", name="ro2", description="ro",
                      problem_class="bug_fix", tool_sequence=["file.read", "file.diff"],
                      confidence=0.8, strategy_type="explicit",
                      created_at_epoch=1.0, updated_at_epoch=1.0)
        s.save(ro)
        stores = {
            "strategy_store": s,
            "outcome_store": OutcomeStore(":memory:"),
            "experience_store": ExperienceStore(":memory:"),
            "learning_store": LearningStore(":memory:"),
            "evidence_store": EvidenceStore(":memory:"),
            "reasoning_store": ReasoningStore(":memory:"),
            "decision_store": DecisionStore(":memory:"),
            "context_store": ContextStore(":memory:"),
        }
        task = {"task_id": "t_ro", "task_type": "bug_fix", "domain": "lint",
                "target": {"file": "src/utils.py", "error": "E302"},
                "description": "Fix E302"}
        res = execute_intelligence_loop(task, workspace_root="/tmp",
                                        knowledge_nodes=KNOWLEDGE, **stores)
        self.assertFalse(res.approval_required)
        self.assertTrue(res.ok)
        for v in stores.values():
            v.close()
