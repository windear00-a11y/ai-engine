"""V1: RuleAdapter deterministic."""

import unittest
from engine.request_adapter import RuleAdapter, parse_request

class RequestAdapterDeterministicTests(unittest.TestCase):
    def test_same_request_same_intent(self):
        a, _ = parse_request("Fix E302 in src/utils.py", workspace_root="/tmp")
        b, _ = parse_request("Fix E302 in src/utils.py", workspace_root="/tmp")
        self.assertEqual(a["intent"], b["intent"])
        self.assertEqual(a["target"], b["target"])
        self.assertEqual(a["request_id"], b["request_id"])
        self.assertEqual(a["confidence"], b["confidence"])

    def test_different_request_different_intent(self):
        a, _ = parse_request("Fix E302 in src/utils.py")
        b, _ = parse_request("Run tests in tests/test_utils.py")
        self.assertNotEqual(a["intent"], b["intent"])

    def test_prefix(self):
        intent, _ = parse_request("Fix E302 in src/utils.py")
        self.assertTrue(intent["request_id"].startswith("rq_"))
        self.assertEqual(len(intent["request_id"]), 3+16)

    def test_rule_adapter_callable(self):
        adapter = RuleAdapter()
        a, _ = adapter("Fix E302 in src/utils.py")
        b, _ = adapter.parse("Fix E302 in src/utils.py")
        self.assertEqual(a["intent"], b["intent"])
