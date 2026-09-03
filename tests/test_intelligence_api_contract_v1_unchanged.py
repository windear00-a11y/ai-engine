"""Phase 10: Contract v1 is unaffected by intelligence API."""

import unittest


class IntelligenceAPIContractV1UnchangedTests(unittest.TestCase):
    def test_contract_v1_operations_unchanged(self):
        from api.contract import OPERATIONS, CONTRACT_VERSION
        self.assertEqual(CONTRACT_VERSION, "1")
        expected = {"search", "get", "related", "follow", "provenance", "inspect"}
        self.assertEqual(set(OPERATIONS.keys()), expected)

    def test_contract_v1_validation_still_works(self):
        from api.contract import validate_request
        op, args = validate_request({"operation": "search", "arguments": {"query": "test", "limit": 5}})
        self.assertEqual(op, "search")
        self.assertEqual(args["query"], "test")

    def test_intelligence_contract_separate(self):
        from intelligence.api.contract import OPERATIONS as INT_OPS, INTELLIGENCE_CONTRACT_VERSION
        from api.contract import OPERATIONS as V1_OPS
        self.assertEqual(INTELLIGENCE_CONTRACT_VERSION, "1")
        # Intelligence ops are disjoint from V1 ops (no overlap in names except maybe 'get' but namespaced)
        self.assertTrue(all("." in op for op in INT_OPS))
        self.assertTrue(all("." not in op for op in V1_OPS))

    def test_intelligence_api_does_not_import_v1_mutations(self):
        # Ensure intelligence handler does not import any write executors
        import pathlib
        text = pathlib.Path("intelligence/api/handler.py").read_text()
        self.assertNotIn("file.write", text)
        self.assertNotIn("KnowledgeRepository.update", text)
