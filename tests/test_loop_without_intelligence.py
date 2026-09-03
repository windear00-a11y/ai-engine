"""Phase 9: loop degrades gracefully if intelligence disabled."""

import unittest

from intelligence.loop import execute_intelligence_loop
from intelligence.outcome.store import OutcomeStore
from intelligence.experience.store import ExperienceStore
from intelligence.learning.store import LearningStore
from intelligence.evidence.store import EvidenceStore


TASK = {"task_id": "t_no_intel", "task_type": "bug_fix", "domain": "lint",
        "target": {"file": "src/utils.py", "error": "E302"},
        "description": "Fix E302"}


class LoopWithoutIntelligenceTests(unittest.TestCase):
    def test_fallback_when_intelligence_disabled(self):
        stores = {
            "outcome_store": OutcomeStore(":memory:"),
            "experience_store": ExperienceStore(":memory:"),
            "learning_store": LearningStore(":memory:"),
            "evidence_store": EvidenceStore(":memory:"),
        }
        res = execute_intelligence_loop(TASK, workspace_root="/tmp",
                                        intelligence_enabled=False, **stores)
        self.assertTrue(res.fallback_used)
        self.assertTrue(res.ok)
        self.assertIsNotNone(res.plan_id)
        self.assertIsNone(res.reasoning_id)
        self.assertIsNone(res.decision_id)
        for v in stores.values():
            v.close()

    def test_fallback_produces_plan(self):
        stores = {
            "outcome_store": OutcomeStore(":memory:"),
            "experience_store": ExperienceStore(":memory:"),
            "learning_store": LearningStore(":memory:"),
            "evidence_store": EvidenceStore(":memory:"),
        }
        res = execute_intelligence_loop(TASK, workspace_root="/tmp",
                                        intelligence_enabled=False, **stores)
        self.assertIsNotNone(res.plan_id)
        for v in stores.values():
            v.close()
