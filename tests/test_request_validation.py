"""V1: request validation."""

import unittest
from engine.request_contract import validate_request, RequestValidationError

class RequestValidationTests(unittest.TestCase):
    def test_valid(self):
        op, args = validate_request({"request": "Fix E302 in src/utils.py"})
        self.assertEqual(op, "request.execute")
        self.assertEqual(args["request"], "Fix E302 in src/utils.py")

    def test_empty_rejected(self):
        with self.assertRaises(RequestValidationError):
            validate_request({"request": "   "})
        with self.assertRaises(RequestValidationError):
            validate_request({"request": ""})

    def test_unknown_intent_not_here_rejected_at_adapter_not_here(self):
        # Validation only checks request string, not intent; unknown intent is handled by adapter fallback to generic, not rejected
        op, args = validate_request({"request": "Do something weird"})
        self.assertEqual(args["request"], "Do something weird")

    def test_extra_keys_rejected(self):
        with self.assertRaises(RequestValidationError):
            validate_request({"request": "hi", "approved": True})
        with self.assertRaises(RequestValidationError):
            validate_request({"request": "hi", "operator_approval": True})
        with self.assertRaises(RequestValidationError):
            validate_request({"request": "hi", "unknown": "x"})

    def test_constraints_clamping(self):
        op, args = validate_request({"request": "hi", "constraints": {"max_steps": 5, "allow_write": True}})
        self.assertEqual(args["constraints"]["max_steps"], 5)

    def test_forbidden_keys_in_arguments(self):
        with self.assertRaises(RequestValidationError):
            validate_request({"operation": "request.execute", "arguments": {"request": "hi", "approved": True}})

    def test_missing_request(self):
        with self.assertRaises(RequestValidationError):
            validate_request({"workspace_root": "/tmp"})
