"""Compatibility tests: the documented Public Contract v1 matches the engine.

These tests prove that everything specified in ``docs/public-api-v1.md`` is
true of the actual implementation:

* the six operations exist and behave as documented;
* request validation (types, defaults, limits, whitelisting) matches;
* response structure matches the envelope/result shapes documented;
* every documented stable error code is produced where the contract says;
* output is deterministic and read-only;
* the SDK and the tool interface agree with each other and with the contract;
* responses expose ``contract_version`` consistent with the contract.

The tests import the SDK and the public tool interface, plus the repository
ONLY as a fixture builder (never as a production path). They never touch the
production database.
"""

import hashlib
import json
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.contract import (
    CONTRACT_VERSION,
    DEFAULT_SEARCH_LIMIT,
    MAX_RESULTS,
    OPERATIONS,
    VALID_NODE_TYPES,
    VALID_RELATIONSHIP_KINDS,
)
from api.tools import ToolInterface
from retrieval.repository import KnowledgeRepository

import knowledge_client  # noqa: E402
from knowledge_client import (  # noqa: E402
    CONTRACT_VERSION as SDK_CONTRACT_VERSION,
    InvalidArgumentError,
    InvalidRelationshipTypeError,
    KnowledgeClient,
    SessionTransport,
    TransportError,
    UnknownOperationError,
)

DOCUMENTED_OPERATIONS = (
    "search", "get", "related", "follow", "provenance", "inspect",
)
DOCUMENTED_ERROR_CODES = (
    "invalid_request", "unknown_operation", "invalid_argument",
    "invalid_relationship_type", "node_not_found", "internal_error",
)
DOCUMENTED_NODE_TYPES = (
    "concept", "technology", "entity", "procedure",
    "rule", "example", "dependency",
    "person", "company", "product", "document",
    "event", "research_paper", "location", "discipline",
)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _seed_db(db_path):
    """Small deterministic fixture DB with traversal opportunities."""
    repo = KnowledgeRepository(db_path)
    repo.initialize()
    sid = repo.add_source("contract-fixture", version="1.0")
    repo.add_node("transport", "entity", "Transport(Write, Read)",
                  "I/O transport with read and write halves", source_id=sid)
    repo.add_node("write-transport", "entity", "WriteTransport",
                  "Write side", source_id=sid)
    repo.add_node("read-transport", "entity", "ReadTransport",
                  "Read side", source_id=sid)
    repo.add_node("log", "entity", "Usage", "Logging", source_id=sid)
    repo.add_relationship("transport", "extends", "write-transport", "cr")
    repo.add_relationship("transport", "extends", "read-transport", "cr")
    repo.add_relationship("read-transport", "references", "log", "cr")
    repo.close()


def _temp_db():
    tmp = tempfile.TemporaryDirectory()
    db = os.path.join(tmp.name, "knowledge.db")
    _seed_db(db)
    return tmp, db


class ContractDefinitionTests(unittest.TestCase):
    """The documented contract is exactly what the engine exposes."""

    def test_documented_operations_match_engine(self):
        self.assertEqual(tuple(OPERATIONS), DOCUMENTED_OPERATIONS)

    def test_contract_version_is_single_authoritative_value(self):
        self.assertEqual(CONTRACT_VERSION, "1")
        self.assertEqual(SDK_CONTRACT_VERSION, CONTRACT_VERSION)

    def test_documented_node_types_match_engine(self):
        self.assertEqual(set(VALID_NODE_TYPES), set(DOCUMENTED_NODE_TYPES))

    def test_relationship_kinds_nonempty(self):
        self.assertTrue(VALID_RELATIONSHIP_KINDS)

    def test_documented_limits_match_engine(self):
        self.assertEqual(DEFAULT_SEARCH_LIMIT, 20)
        self.assertEqual(MAX_RESULTS, 100)


class ToolInterfaceContractTests(unittest.TestCase):
    """The tool interface obeys the envelope contract for every operation."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self.interface = ToolInterface(db_path=self.db)
        self.hash_before = _sha256(self.db)

    def tearDown(self):
        self.interface.close()
        self.tmp.cleanup()

    def _result(self, request):
        res = self.interface.execute(request)
        self.assertTrue(res["ok"], res)
        return res

    def test_every_operation_matches_documented_contract(self):
        # search
        res = self._result({"operation": "search",
                            "arguments": {"query": "transport"}})
        self.assertIsInstance(res["result"], list)
        self.assertTrue(res["result"])
        # get
        node = self._result({"operation": "get",
                             "arguments": {"node_id": "transport"}})["result"]
        self.assertEqual(node["id"], "transport")
        self.assertIn("type", node)
        self.assertIn("name", node)
        # related
        related = self._result({"operation": "related",
                                "arguments": {"node_id": "transport"}})["result"]
        self.assertIsInstance(related, list)
        self.assertTrue(related)
        for entry in related:
            self.assertIn("node", entry)
            self.assertIn("via", entry)
            for via in entry["via"]:
                self.assertIn("relationship_type", via)
                self.assertIn("direction", via)
        # follow
        edges = self._result({"operation": "follow",
                              "arguments": {
                                  "node_id": "transport",
                                  "relationship_type": "extends"}})["result"]
        self.assertEqual({e["target_node_id"] for e in edges},
                         {"write-transport", "read-transport"})
        for e in edges:
            self.assertIn("relationship_type", e)
            self.assertIn("target_node_id", e)
            self.assertIn("node", e)
        # provenance
        prov = self._result({"operation": "provenance",
                             "arguments": {
                                 "node_id": "transport"}})["result"]
        self.assertEqual(prov["node_id"], "transport")
        for key in ("source_id", "source_name", "source_version",
                    "source_location", "imported_at"):
            self.assertIn(key, prov)
        # inspect
        stats = self._result({"operation": "inspect"})["result"]
        for key in ("source_count", "node_count", "relationship_count",
                    "nodes_by_type", "relationships_by_type"):
            self.assertIn(key, stats)
        self.assertEqual(stats["node_count"], 4)

    def test_contract_version_on_envelope(self):
        for request in (
            {"operation": "inspect"},
            {"operation": "get", "arguments": {"node_id": "transport"}},
            {"operation": "search", "arguments": {"query": "transport"}},
        ):
            res = self.interface.execute(request)
            self.assertEqual(res["contract_version"], CONTRACT_VERSION)
        bad = self.interface.execute(
            {"operation": "get", "arguments": {"node_id": "missing"}})
        self.assertEqual(bad["contract_version"], CONTRACT_VERSION)
        bad2 = self.interface.execute({"operation": "nope"})
        self.assertEqual(bad2["contract_version"], CONTRACT_VERSION)

    def test_documented_error_codes_produced(self):
        def code(request):
            return self.interface.execute(request)["error"]["code"]

        self.assertEqual(code(None), "invalid_request")
        self.assertEqual(code([]), "invalid_request")
        self.assertEqual(code({"operation": "nope"}), "unknown_operation")
        self.assertEqual(
            code({"operation": "search", "arguments": {}}),
            "invalid_argument")
        self.assertEqual(
            code({"operation": "search",
                  "arguments": {"query": "x", "limit": -1}}),
            "invalid_argument")
        self.assertEqual(
            code({"operation": "get",
                  "arguments": {"node_id": "missing"}}),
            "node_not_found")
        self.assertEqual(
            code({"operation": "follow",
                  "arguments": {"node_id": "transport",
                                "relationship_type": "is_a"}}),
            "invalid_relationship_type")
        self.assertEqual(
            code({"operation": "search",
                  "arguments": {"query": "x", "sql": "SELECT 1"}}),
            "invalid_argument")

    def test_no_stack_traces_in_errors(self):
        for request in (None, {"operation": "nope"},
                        {"operation": "get",
                         "arguments": {"node_id": "missing"}}):
            res = self.interface.execute(request)
            serialized = json.dumps(res)
            self.assertNotIn("Traceback", serialized)
            self.assertNotIn("File \"", serialized)

    def test_deterministic_output(self):
        a = self.interface.execute({"operation": "search",
                                    "arguments": {"query": "transport"}})
        b = self.interface.execute({"operation": "search",
                                    "arguments": {"query": "transport"}})
        self.assertEqual(a, b)

    def test_read_only_database_unchanged(self):
        for op, args in (
            ("search", {"query": "transport"}),
            ("get", {"node_id": "transport"}),
            ("related", {"node_id": "transport"}),
            ("follow", {"node_id": "transport",
                        "relationship_type": "extends"}),
            ("provenance", {"node_id": "transport"}),
            ("inspect", {}),
        ):
            request: dict = {"operation": op}
            if args:
                request["arguments"] = args
            self.assertTrue(self.interface.execute(request)["ok"], op)
        self.assertEqual(_sha256(self.db), self.hash_before)

    def test_limits_capped(self):
        res = self.interface.execute(
            {"operation": "search",
             "arguments": {"query": "transport", "limit": 5000}})
        self.assertTrue(res["ok"], res)
        self.assertLessEqual(len(res["result"]), MAX_RESULTS)


class SdkContractTests(unittest.TestCase):
    """The SDK agrees with the tool interface and the documented contract."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self.interface = ToolInterface(db_path=self.db)
        self.engine = KnowledgeClient(self.interface)

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def test_interface_and_sdk_agree(self):
        for request in (
            {"operation": "inspect"},
            {"operation": "search",
             "arguments": {"query": "transport", "limit": 5}},
            {"operation": "get", "arguments": {"node_id": "transport"}},
        ):
            via_sdk = self.engine.execute(request)
            via_tool = self.interface.execute(request)
            self.assertEqual(via_sdk, via_tool)
            self.assertEqual(via_sdk.get("contract_version"), CONTRACT_VERSION)

    def test_sdk_typed_operations_match_documented_contract(self):
        hits = self.engine.search("transport", limit=10)
        self.assertIsInstance(hits, list)
        self.assertTrue(hits)
        node = self.engine.get("transport")
        self.assertEqual(node["id"], "transport")
        neighbours = self.engine.related("transport")
        self.assertTrue(neighbours)
        edges = self.engine.follow("transport", relationship_type="extends")
        self.assertEqual({e["target_node_id"] for e in edges},
                         {"write-transport", "read-transport"})
        prov = self.engine.provenance("transport")
        self.assertEqual(prov["node_id"], "transport")
        stats = self.engine.inspect()
        self.assertEqual(stats["node_count"], 4)

    def test_sdk_error_mapping_matches_documented_codes(self):
        with self.assertRaises(UnknownOperationError) as ctx:
            self.engine.request("nope")
        self.assertEqual(ctx.exception.code, "unknown_operation")
        with self.assertRaises(InvalidArgumentError) as ctx:
            self.engine.search("x", limit=-1)
        self.assertEqual(ctx.exception.code, "invalid_argument")
        with self.assertRaises(InvalidRelationshipTypeError):
            self.engine.follow("transport", relationship_type="is_a")
        from knowledge_client import NodeNotFoundError
        with self.assertRaises(NodeNotFoundError) as ctx:
            self.engine.get("missing")
        self.assertEqual(ctx.exception.code, "node_not_found")
        # health: an error does not kill the in-process engine
        self.assertEqual(self.engine.inspect()["node_count"], 4)

    def test_sdk_contract_version_constant(self):
        self.assertEqual(SDK_CONTRACT_VERSION, "1")
        self.assertEqual(SDK_CONTRACT_VERSION, CONTRACT_VERSION)

    def test_sdk_default_search_limit_matches_contract(self):
        hits = self.engine.search("transport")
        self.assertLessEqual(len(hits), DEFAULT_SEARCH_LIMIT)


class SessionContractTests(unittest.TestCase):
    """The persistent session endpoints (real subprocess) match the contract."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self.hash_before = _sha256(self.db)
        self.transport = SessionTransport(db=self.db)
        self.engine = KnowledgeClient(self.transport)

    def tearDown(self):
        self.transport.close()
        self.tmp.cleanup()

    def test_session_envelope_carries_contract_version(self):
        raw = self.engine.execute({"operation": "inspect"})
        self.assertEqual(raw["contract_version"], CONTRACT_VERSION)
        bad = self.engine.execute(
            {"operation": "get", "arguments": {"node_id": "missing"}})
        self.assertEqual(bad["contract_version"], CONTRACT_VERSION)

    def test_session_search_get_provenance_chain(self):
        hits = self.engine.search("ReadTransport", limit=5)
        ids = {h["id"] for h in hits}
        self.assertIn("read-transport", ids)
        node = self.engine.get("read-transport")
        self.assertEqual(node["id"], "read-transport")
        prov = self.engine.provenance("read-transport")
        self.assertEqual(prov["node_id"], "read-transport")

    def test_session_search_related_follow(self):
        hits = self.engine.search("transport", limit=10)
        source_id = next(h["id"] for h in hits if h["id"] == "transport")
        neighbours = self.engine.related(source_id)
        self.assertGreaterEqual(len(neighbours), 1)
        edges = self.engine.follow(source_id, relationship_type="extends")
        self.assertEqual({e["target_node_id"] for e in edges},
                         {"write-transport", "read-transport"})

    def test_session_deterministic_and_read_only(self):
        first = self.engine.search("transport", limit=5)
        second = self.engine.search("transport", limit=5)
        self.assertEqual(first, second)
        from knowledge_client import NodeNotFoundError
        with self.assertRaises(NodeNotFoundError):
            self.engine.get("missing")
        self.assertEqual(self.engine.inspect()["node_count"], 4)
        self.assertEqual(_sha256(self.db), self.hash_before)


if __name__ == "__main__":
    unittest.main()