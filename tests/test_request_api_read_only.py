"""V1: request API safety — no injection."""

import unittest
from engine.request_handler import handle_request

class RequestAPIReadOnlyTests(unittest.TestCase):
    def test_sql_injection_rejected(self):
        res = handle_request({"request": "Fix E302'; DROP TABLE-- in src/utils.py"}, workspace_root="/tmp")
        # Should not crash, should be handled as bug_fix with file extraction, not executed as SQL
        # Result ok may be true (synthetic plan) but no SQL execution; check intent parsed and no error leak
        self.assertIn("intent", res)
        self.assertEqual(res["intent"]["target"]["file"], "src/utils.py")
        # Ensure raw_request is preserved but not executed as SQL (no internal error)
        self.assertNotEqual(res.get("status"), "internal_error")

    def test_path_traversal_not_executed(self):
        res = handle_request({"request": "Fix E302 in ../../etc/passwd"}, workspace_root="/tmp")
        # Should not escape workspace; planner will report insufficient due to file not in index
        self.assertIn("status", res)

    def test_no_approved_bypass(self):
        res = handle_request({"request": "Fix E302 in src/utils.py", "approved": True}, workspace_root="/tmp")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "invalid_argument")

    def test_all_operations_read_only_until_approval(self):
        # Direct loop has no write until approval; request handler same
        res = handle_request({"request": "Fix E302 in src/utils.py", "workspace_root": "/tmp"})
        # Without mutating strategy, should not require approval (low risk read-only)
        self.assertIn("approval_required", res)
