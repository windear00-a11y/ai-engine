"""V1: request loop integration (mocked loop)."""

import unittest
from unittest.mock import patch
from engine.request_handler import handle_request

class RequestLoopIntegrationTests(unittest.TestCase):
    def test_handler_calls_loop_with_intent(self):
        with patch("engine.request_handler.execute_intelligence_loop") as mock_loop:
            from intelligence.loop.types import LoopResult
            mock_loop.return_value = LoopResult(task_id="rq_abc", context_id="ctx_a", reasoning_id="rs_a", decision_id="ds_a", plan_id="plan_a", outcome_id="oc_a", experience_id="xp_a", learning_event_id="le_a", ok=True, status="completed")
            res = handle_request({"request": "Fix E302 in src/utils.py"}, workspace_root="/tmp", knowledge_nodes=[])
            self.assertTrue(res["ok"])
            # Check loop was called with task derived from intent
            args, kwargs = mock_loop.call_args
            task = args[0] if args else kwargs.get("task")
            self.assertEqual(task["task_type"], "bug_fix")
            self.assertEqual(task["target"]["file"], "src/utils.py")

    def test_handler_propagates_insufficient(self):
        with patch("engine.request_handler.execute_intelligence_loop") as mock_loop:
            from intelligence.loop.types import LoopResult
            mock_loop.return_value = LoopResult(task_id="rq_abc", context_id="ctx_a", plan_id="plan_a", outcome_id=None, ok=False, status="insufficient_information", errors=["no target"])
            res = handle_request({"request": "Fix E302"}, workspace_root="/tmp")
            self.assertFalse(res["ok"])
            self.assertIn("insufficient", res["status"])
