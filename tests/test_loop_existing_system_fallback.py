"""Phase 9: if intelligence unavailable, existing system works."""

import unittest

from intelligence.loop import execute_intelligence_loop
from intelligence.outcome.store import OutcomeStore
from intelligence.experience.store import ExperienceStore
from intelligence.learning.store import LearningStore
from intelligence.evidence.store import EvidenceStore


TASK = {"task_id": "t_fallback", "task_type": "bug_fix", "domain": "lint",
        "target": {"file": "src/utils.py", "error": "E302"},
        "description": "Fix E302"}


class LoopExistingSystemFallbackTests(unittest.TestCase):
    def test_fallback_still_produces_plan_and_outcome(self):
        stores = {
            "outcome_store": OutcomeStore(":memory:"),
            "experience_store": ExperienceStore(":memory:"),
            "learning_store": LearningStore(":memory:"),
            "evidence_store": EvidenceStore(":memory:"),
        }
        res = execute_intelligence_loop(TASK, workspace_root="/tmp",
                                        intelligence_enabled=False, **stores)
        self.assertTrue(res.fallback_used)
        self.assertIsNotNone(res.plan_id)
        self.assertIsNotNone(res.outcome_id)
        self.assertIsNotNone(res.experience_id)
        self.assertTrue(res.ok)
        for v in stores.values():
            v.close()

    def test_fallback_does_not_crash_on_missing_stores(self):
        # No stores at all, intelligence disabled
        res = execute_intelligence_loop(TASK, workspace_root="/tmp",
                                        intelligence_enabled=False)
        self.assertTrue(res.ok)
        self.assertIsNotNone(res.plan_id)

    def test_intelligence_failure_degrades_gracefully(self):
        # Pass a broken knowledge retriever by monkey-patching retrieve to raise
        import intelligence.loop.retrieve as retr
        orig = retr.retrieve_knowledge
        try:
            def broken(*a, **kw):
                raise RuntimeError("knowledge unavailable")
            retr.retrieve_knowledge = broken
            stores = {
                "outcome_store": OutcomeStore(":memory:"),
                "experience_store": ExperienceStore(":memory:"),
                "learning_store": LearningStore(":memory:"),
                "evidence_store": EvidenceStore(":memory:"),
            }
            res = execute_intelligence_loop(TASK, workspace_root="/tmp", **stores)
            # Should fallback, not crash, still produce plan/outcome
            self.assertIsNotNone(res.plan_id)
            self.assertIsNotNone(res.outcome_id)
        finally:
            retr.retrieve_knowledge = orig
            for v in stores.values():
                v.close()
