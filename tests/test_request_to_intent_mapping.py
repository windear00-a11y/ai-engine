"""V1: request to intent mapping."""

import unittest
from engine.request_adapter import parse_request

class RequestToIntentMappingTests(unittest.TestCase):
    def test_e302_bug_fix(self):
        intent, _ = parse_request("Fix E302 in src/utils.py")
        self.assertEqual(intent["intent"], "bug_fix")
        self.assertEqual(intent["target"]["file"], "src/utils.py")
        self.assertEqual(intent["error"]["message"], "E302")

    def test_test_verify(self):
        intent, _ = parse_request("Run tests in tests/test_utils.py")
        self.assertEqual(intent["intent"], "test_verify")
        self.assertIn("tests/test_utils.py", intent["target"]["file"])

    def test_generic_fallback(self):
        intent, _ = parse_request("Do something generic")
        self.assertEqual(intent["intent"], "generic")

    def test_lint_bug_fix(self):
        intent, _ = parse_request("Fix lint error E302 in src/helpers.py")
        self.assertEqual(intent["intent"], "bug_fix")

    def test_confidence_higher_with_file_and_error(self):
        a, _ = parse_request("Fix E302 in src/utils.py")
        b, _ = parse_request("Fix something")
        self.assertGreater(a["confidence"], b["confidence"])
