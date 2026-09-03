"""V1: approval flow via RequestHandler."""

import unittest, tempfile, os, shutil
from engine.request_handler import handle_request
from tools.indexer.indexer import ProjectIndex
from intelligence.strategy.store import StrategyStore
from intelligence.strategy.schema import Strategy
from intelligence.strategy.registry import initialize_strategies_from_planner

class RequestApprovalFlowTests(unittest.TestCase):
    def setUp(self):
        self.ws = tempfile.mkdtemp(prefix="req_approve_")
        os.makedirs(os.path.join(self.ws, "src"), exist_ok=True)
        with open(os.path.join(self.ws, "src/utils.py"), "w") as f:
            f.write('def foo():\n    pass\ndef bar():\n    pass\n')
        os.makedirs(os.path.join(self.ws, ".ai-engine"), exist_ok=True)
        idx = ProjectIndex(self.ws)
        idx.build()
        self.val_dir = os.path.join(self.ws, ".ai-engine/validation")
        os.makedirs(self.val_dir, exist_ok=True)
        self.stores = {
            "strategy_store": StrategyStore(os.path.join(self.val_dir, "evidence.db")),
            "outcome_store": __import__("intelligence.outcome.store", fromlist=["OutcomeStore"]).OutcomeStore(os.path.join(self.val_dir, "evidence.db")),
            "experience_store": __import__("intelligence.experience.store", fromlist=["ExperienceStore"]).ExperienceStore(os.path.join(self.val_dir, "experience.db")),
            "learning_store": __import__("intelligence.learning.store", fromlist=["LearningStore"]).LearningStore(os.path.join(self.val_dir, "evidence.db")),
            "evidence_store": __import__("intelligence.evidence.store", fromlist=["EvidenceStore"]).EvidenceStore(os.path.join(self.val_dir, "evidence.db")),
            "reasoning_store": __import__("intelligence.reasoning.store", fromlist=["ReasoningStore"]).ReasoningStore(os.path.join(self.val_dir, "evidence.db")),
            "decision_store": __import__("intelligence.decision.store", fromlist=["DecisionStore"]).DecisionStore(os.path.join(self.val_dir, "evidence.db")),
            "context_store": __import__("intelligence.context.store", fromlist=["ContextStore"]).ContextStore(os.path.join(self.val_dir, "context.db")),
        }
        initialize_strategies_from_planner(store=self.stores["strategy_store"], created_at_epoch=1.0)
        # Add mutating high-confidence strategy to force approval
        mut = Strategy(strategy_id="st_mut_approve", name="mut", description="mut", problem_class="bug_fix", tool_sequence=["file.write","project.test"], confidence=0.95, strategy_type="explicit", created_at_epoch=1.0, updated_at_epoch=1.0)
        self.stores["strategy_store"].save(mut)
        self.knowledge = [{"id": "k1", "type": "fact", "name": "E302", "description": "E302", "subject": "e302", "lifecycle": {"confidence": 0.85}}]

    def tearDown(self):
        for v in self.stores.values():
            try: v.close()
            except: pass
        shutil.rmtree(self.ws, ignore_errors=True)

    def test_without_approval_awaits(self):
        res = handle_request({"request": "Fix E302 in src/utils.py"}, workspace_root=self.ws, stores=self.stores, knowledge_nodes=self.knowledge, operator_approval=lambda d: False)
        self.assertEqual(res["status"], "awaiting_approval")
        self.assertTrue(res["approval_required"])
        self.assertFalse(res["ok"])
        # File unchanged
        with open(os.path.join(self.ws, "src/utils.py")) as f:
            self.assertNotIn("pass\n\ndef", f.read())

    def test_with_approval_mutates(self):
        res = handle_request({"request": "Fix E302 in src/utils.py"}, workspace_root=self.ws, stores=self.stores, knowledge_nodes=self.knowledge, operator_approval=lambda d: True)
        self.assertTrue(res["ok"])
        self.assertEqual(res["status"], "completed")
        with open(os.path.join(self.ws, "src/utils.py")) as f:
            self.assertIn("pass\n\ndef", f.read())

    def test_approved_flag_ignored(self):
        # Client smuggling approved=true should be rejected, not treated as authority
        res = handle_request({"request": "Fix E302 in src/utils.py", "approved": True}, workspace_root=self.ws, stores=self.stores, knowledge_nodes=self.knowledge)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "invalid_argument")
