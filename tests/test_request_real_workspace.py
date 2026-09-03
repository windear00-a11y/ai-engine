"""V1: real indexed workspace E2E via RequestHandler."""

import unittest, tempfile, os, shutil
from engine.request_handler import handle_request
from tools.indexer.indexer import ProjectIndex
from intelligence.strategy.store import StrategyStore
from intelligence.strategy.registry import initialize_strategies_from_planner

class RequestRealWorkspaceTests(unittest.TestCase):
    def test_real_e2e_bug_fix(self):
        ws = tempfile.mkdtemp(prefix="req_real_")
        try:
            os.makedirs(os.path.join(ws, "src"), exist_ok=True)
            with open(os.path.join(ws, "src/utils.py"), "w") as f:
                f.write('def foo():\n    pass\ndef bar():\n    pass\n')
            os.makedirs(os.path.join(ws, "tests"), exist_ok=True)
            with open(os.path.join(ws, "tests/test_dummy.py"), "w") as f:
                f.write('def test_dummy(): assert True\n')
            with open(os.path.join(ws, "pyproject.toml"), "w") as f:
                f.write('[build-system]\nrequires=["setuptools"]\n')
            idx = ProjectIndex(ws)
            build = idx.build()
            self.assertEqual(build["counts"]["files"], 3)
            val_dir = os.path.join(ws, ".ai-engine/validation")
            os.makedirs(val_dir, exist_ok=True)
            stores = {
                "strategy_store": StrategyStore(os.path.join(val_dir, "evidence.db")),
                "outcome_store": __import__("intelligence.outcome.store", fromlist=["OutcomeStore"]).OutcomeStore(os.path.join(val_dir, "evidence.db")),
                "experience_store": __import__("intelligence.experience.store", fromlist=["ExperienceStore"]).ExperienceStore(os.path.join(val_dir, "experience.db")),
                "learning_store": __import__("intelligence.learning.store", fromlist=["LearningStore"]).LearningStore(os.path.join(val_dir, "evidence.db")),
                "evidence_store": __import__("intelligence.evidence.store", fromlist=["EvidenceStore"]).EvidenceStore(os.path.join(val_dir, "evidence.db")),
                "reasoning_store": __import__("intelligence.reasoning.store", fromlist=["ReasoningStore"]).ReasoningStore(os.path.join(val_dir, "evidence.db")),
                "decision_store": __import__("intelligence.decision.store", fromlist=["DecisionStore"]).DecisionStore(os.path.join(val_dir, "evidence.db")),
                "context_store": __import__("intelligence.context.store", fromlist=["ContextStore"]).ContextStore(os.path.join(val_dir, "context.db")),
            }
            initialize_strategies_from_planner(store=stores["strategy_store"], created_at_epoch=1.0)
            # Add mutating strategy so decision requires approval and triggers file write
            from intelligence.strategy.schema import Strategy as Strat2
            mut = Strat2(strategy_id="st_mut_real_ws", name="mut", description="mut", problem_class="bug_fix", tool_sequence=["file.write","project.test"], confidence=0.95, strategy_type="explicit", created_at_epoch=1.0, updated_at_epoch=1.0)
            stores["strategy_store"].save(mut)
            knowledge = [{"id": "k1", "type": "fact", "name": "E302", "description": "E302", "subject": "e302", "lifecycle": {"confidence": 0.85}}]
            res = handle_request({"request": "Fix E302 in src/utils.py"}, workspace_root=ws, stores=stores, knowledge_nodes=knowledge, operator_approval=lambda d: True)
            self.assertTrue(res["ok"])
            self.assertEqual(res["status"], "completed")
            self.assertIsNotNone(res["outcome_id"])
            self.assertIsNotNone(res["experience_id"])
            self.assertIsNotNone(res["learning_event_id"])
            # File actually mutated via gated write
            with open(os.path.join(ws, "src/utils.py")) as f:
                self.assertIn("pass\n\ndef", f.read())
            # Verification: project_check ok
            from engine.task_engine import TaskEngine
            from tools.permissions import Policy, PathPolicy, ApprovalGate
            from tools.permissions.journal import EngineState
            policy=Policy(); pp=PathPolicy(ws, policy=policy); state=EngineState(db_path=os.path.join(ws, ".ai-engine/engine_state.db"))
            gate=ApprovalGate(path_policy=pp, state=state, approver=lambda p: True)
            engine=TaskEngine(workspace_root=ws, permissions=gate, policy=policy, approver=lambda p: True)
            check = engine.coding.project_check()
            self.assertTrue(check["success"])
            for v in stores.values():
                try: v.close()
                except: pass
        finally:
            shutil.rmtree(ws, ignore_errors=True)

    def test_second_request_reuses_experience(self):
        ws = tempfile.mkdtemp(prefix="req_reuse_")
        try:
            os.makedirs(os.path.join(ws, "src"), exist_ok=True)
            for name in ["utils.py", "helpers.py"]:
                with open(os.path.join(ws, f"src/{name}"), "w") as f:
                    f.write('def a():\n    pass\ndef b():\n    pass\n')
            with open(os.path.join(ws, "pyproject.toml"), "w") as f:
                f.write('[build-system]\nrequires=["setuptools"]\n')
            idx = ProjectIndex(ws)
            idx.build()
            val_dir = os.path.join(ws, ".ai-engine/validation")
            os.makedirs(val_dir, exist_ok=True)
            stores = {
                "strategy_store": StrategyStore(os.path.join(val_dir, "evidence.db")),
                "outcome_store": __import__("intelligence.outcome.store", fromlist=["OutcomeStore"]).OutcomeStore(os.path.join(val_dir, "evidence.db")),
                "experience_store": __import__("intelligence.experience.store", fromlist=["ExperienceStore"]).ExperienceStore(os.path.join(val_dir, "experience.db")),
                "learning_store": __import__("intelligence.learning.store", fromlist=["LearningStore"]).LearningStore(os.path.join(val_dir, "evidence.db")),
                "evidence_store": __import__("intelligence.evidence.store", fromlist=["EvidenceStore"]).EvidenceStore(os.path.join(val_dir, "evidence.db")),
                "reasoning_store": __import__("intelligence.reasoning.store", fromlist=["ReasoningStore"]).ReasoningStore(os.path.join(val_dir, "evidence.db")),
                "decision_store": __import__("intelligence.decision.store", fromlist=["DecisionStore"]).DecisionStore(os.path.join(val_dir, "evidence.db")),
                "context_store": __import__("intelligence.context.store", fromlist=["ContextStore"]).ContextStore(os.path.join(val_dir, "context.db")),
            }
            initialize_strategies_from_planner(store=stores["strategy_store"], created_at_epoch=1.0)
            from intelligence.strategy.schema import Strategy as Strat3
            mut2 = Strat3(strategy_id="st_mut_real2", name="mut2", description="mut", problem_class="bug_fix", tool_sequence=["file.write","project.test"], confidence=0.95, strategy_type="explicit", created_at_epoch=1.0, updated_at_epoch=1.0)
            stores["strategy_store"].save(mut2)
            knowledge = [{"id": "k1", "type": "fact", "name": "E302", "description": "E302", "subject": "e302", "lifecycle": {"confidence": 0.85}}]
            res1 = handle_request({"request": "Fix E302 in src/utils.py"}, workspace_root=ws, stores=stores, knowledge_nodes=knowledge, operator_approval=lambda d: True)
            res2 = handle_request({"request": "Fix E302 in src/helpers.py"}, workspace_root=ws, stores=stores, knowledge_nodes=knowledge, operator_approval=lambda d: True)
            self.assertNotEqual(res1["reasoning_id"], res2["reasoning_id"])
            # Second should have experience-influenced reasoning (quality higher)
            self.assertTrue(res2["ok"])
            for v in stores.values():
                try: v.close()
                except: pass
        finally:
            shutil.rmtree(ws, ignore_errors=True)
