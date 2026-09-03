"""V1: fallback without intelligence and non-indexed workspace."""

import unittest
from engine.request_handler import handle_request
from intelligence.strategy.store import StrategyStore

class RequestFallbackTests(unittest.TestCase):
    def test_non_indexed_fallback_still_ok(self):
        # No project index, no intelligence stores with data — should fallback but still produce plan
        res = handle_request({"request": "Fix E302 in src/utils.py"}, workspace_root="/tmp", knowledge_nodes=[])
        # With empty knowledge and no index, loop will use synthetic planner fallback and still be ok (project.inspect)
        self.assertIn(res["status"], ("completed", "fallback"))

    def test_insufficient_information(self):
        # Generic without target and no file -> planner insufficient
        res = handle_request({"request": "Fix something"}, workspace_root="/tmp", knowledge_nodes=[])
        # Should not crash, may be fallback or insufficient
        self.assertIn("status", res)

    def test_existing_system_without_intelligence_still_usable(self):
        from tools.planner.deterministic import DeterministicPlanner
        import tempfile, os, shutil
        ws = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(ws, "src"), exist_ok=True)
            with open(os.path.join(ws, "src/a.py"), "w") as f:
                f.write("x=1\n")
            os.makedirs(os.path.join(ws, ".ai-engine"), exist_ok=True)
            from tools.indexer.indexer import ProjectIndex
            idx = ProjectIndex(ws)
            idx.build()
            planner = DeterministicPlanner(ws)
            plan = planner.generate(intent="generic", target={"file":"src/a.py"}, error={}, constraints={})
            self.assertEqual(plan["planner_status"], "ok")
        finally:
            shutil.rmtree(ws, ignore_errors=True)
