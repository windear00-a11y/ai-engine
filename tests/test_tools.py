"""Tests for the external tool-call interface (``api.tools`` + ``api.tract``).

Covers:
* the transport-independent request/response contract (structured dicts,
  stable error codes, no exceptions leaking);
* every operation (search, get, related, follow, provenance, inspect) against
  a small seeded database;
* a real-world traversal (follow ``extends`` two hops) and a "read-only,
  hash unchanged" guarantee across the runner;
* strict validation: unknown operations, missing/extra/wrong-typed arguments,
  invalid limits, invalid relationship types, and attempts to smuggle
  ``sql``/``path``/``command`` keys are all rejected with ``ok: false``;
* the local runner (``python -m api.tools``) file & stdin input, JSON output,
  exit codes, and rejection of non-object / unmarshallable input.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.contract import (
    MAX_RESULTS,
    OPERATIONS,
    VALID_NODE_TYPES,
    VALID_RELATIONSHIP_KINDS,
    validate_request,
)
from api.errors import (
    KnowledgeArgumentError,
    RelationshipTypeError,
    ToolRequestError,
    UnknownOperationError,
)
from api.tools import ToolInterface, execute
from retrieval.repository import KnowledgeRepository


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _seed_traversal_db(db_path):
    """Write-side, read-side, log and traversal relationships."""
    repo = KnowledgeRepository(db_path)
    repo.initialize()
    sid = repo.add_source("traversal-source", version="1.0",
                          location="/tmp/traversal.json")
    repo.add_node(
        "transport", "entity", "Transport(WriteTransport, ReadTransport)",
        "An I/O transport exposing read and write halves", source_id=sid,
        metadata={"protocols": ["tcp"]})
    repo.add_node("write-transport", "entity", "WriteTransport",
                  "Write side of a transport", source_id=sid)
    repo.add_node("read-transport", "entity", "ReadTransport",
                  "Read side of a transport", source_id=sid)
    repo.add_node("buffered-transport", "entity", "BufferedTransport",
                  "A buffered transport", source_id=sid)
    repo.add_node("log", "entity", "Usage", "Logging facilities", source_id=sid)
    repo.add_relationship("transport", "extends",
                          "write-transport", "traversal")
    repo.add_relationship("transport", "extends", "read-transport", "traversal")
    repo.add_relationship("transport", "related_to",
                          "buffered-transport", "traversal")
    repo.add_relationship("read-transport", "references", "log", "traversal")
    repo.close()


def _seed_bulk_db(db_path, count=130):
    """Many nodes matching one query, to exercise the result cap."""
    repo = KnowledgeRepository(db_path)
    repo.initialize()
    sid = repo.add_source("bulk-source", version="1.0")
    for i in range(count):
        repo.add_node("bulk-%03d" % i, "concept", "Bulk Node %d" % i,
                      "bulk matching text", source_id=sid)
    repo.close()


def _temp_seeded(seed=_seed_traversal_db):
    tmp = tempfile.TemporaryDirectory()
    db = os.path.join(tmp.name, "knowledge.db")
    seed(db)
    return tmp, db


def _run_runner(*args, stdin=None):
    return subprocess.run(
        [sys.executable, "-m", "api.tools", *args],
        cwd=_ROOT, capture_output=True, text=True, input=stdin)


class ToolInterfaceTests(unittest.TestCase):
    def test_every_operation_returns_ok_true(self):
        tmp, db = _temp_seeded()
        try:
            interface = ToolInterface(db_path=db)
            try:
                responses = (
                    interface.execute({"operation": "inspect"}),
                    interface.execute({"operation": "get",
                                       "arguments": {"node_id": "transport"}}),
                    interface.execute({"operation": "search",
                                       "arguments": {"query": "transport"}}),
                    interface.execute({"operation": "related",
                                       "arguments": {"node_id": "transport"}}),
                    interface.execute({"operation": "follow",
                                       "arguments": {"node_id": "transport",
                                                     "relationship_type":
                                                     "extends"}}),
                    interface.execute({"operation": "provenance",
                                       "arguments": {
                                           "node_id": "read-transport"}}),
                )
            finally:
                interface.close()
            for res in responses:
                self.assertTrue(res["ok"], res)
            self.assertEqual(responses[0]["result"]["source_count"], 1)
            self.assertEqual(responses[1]["result"]["id"], "transport")
            self.assertTrue(responses[2]["result"])
            self.assertEqual({e["target_node_id"]
                              for e in responses[4]["result"]},
                             {"write-transport", "read-transport"})
        finally:
            tmp.cleanup()

    def test_interface_never_raises_for_bad_requests(self):
        tmp, db = _temp_seeded()
        try:
            interface = ToolInterface(db_path=db)
            try:
                cases = [
                    (None, "invalid_request"),
                    ([], "invalid_request"),
                    ("text", "invalid_request"),
                    (42, "invalid_request"),
                    ({"operation": 7}, "invalid_request"),
                    ({}, "invalid_request"),
                    ({"operation": "nope"}, "unknown_operation"),
                    ({"operation": "search"}, "invalid_argument"),
                    ({"operation": "search", "arguments": "x"},
                     "invalid_request"),
                    ({"operation": "search",
                      "arguments": {"query": "x", "sql": "SELECT 1"}},
                     "invalid_argument"),
                ]
                for request, expected in cases:
                    res = interface.execute(request)
                    self.assertFalse(res["ok"], request)
                    self.assertEqual(res["error"]["code"], expected, request)
            finally:
                interface.close()
        finally:
            tmp.cleanup()


class TraversalTests(unittest.TestCase):
    def setUp(self):
        self.tmp, self.db = _temp_seeded()
        self.interface = ToolInterface(db_path=self.db)

    def tearDown(self):
        self.interface.close()
        self.tmp.cleanup()

    def _ok(self, request):
        res = self.interface.execute(request)
        self.assertTrue(res["ok"], res)
        return res["result"]

    def test_follow_extends_returns_two_sides(self):
        result = self._ok({"operation": "follow",
                           "arguments": {"node_id": "transport",
                                         "relationship_type": "extends"}})
        self.assertEqual(len(result), 2)
        self.assertEqual({e["target_node_id"] for e in result},
                         {"write-transport", "read-transport"})

    def test_follow_related_single_hop(self):
        result = self._ok({"operation": "follow",
                           "arguments": {"node_id": "transport",
                                         "relationship_type": "related_to"}})
        self.assertEqual([e["target_node_id"] for e in result],
                         ["buffered-transport"])

    def test_follow_references_links_read_side_to_log(self):
        result = self._ok({"operation": "follow",
                           "arguments": {"node_id": "read-transport",
                                         "relationship_type": "references"}})
        self.assertEqual([e["target_node_id"] for e in result], ["log"])

    def test_follow_missing_type_yields_empty(self):
        result = self._ok({"operation": "follow",
                           "arguments": {"node_id": "read-transport",
                                         "relationship_type": "extends"}})
        self.assertEqual(result, [])

    def test_two_hop_traversal_write_side_to_log(self):
        source = self._ok({"operation": "follow",
                           "arguments": {"node_id": "transport",
                                         "relationship_type": "extends"}})
        ids = {e["target_node_id"] for e in source}
        assert "read-transport" in ids
        final = self._ok({"operation": "follow",
                          "arguments": {"node_id": "read-transport",
                                        "relationship_type": "references"}})
        self.assertEqual([e["target_node_id"] for e in final], ["log"])

    def test_traversal_start_node_discovered_by_search(self):
        result = self._ok({"operation": "search",
                           "arguments": {"query": "I/O",
                                         "node_type": "entity"}})
        self.assertTrue(result)
        source_id = result[0]["id"]
        follow = self._ok({"operation": "follow",
                           "arguments": {"node_id": source_id,
                                         "relationship_type": "extends"}})
        self.assertEqual({e["target_node_id"] for e in follow},
                         {"write-transport", "read-transport"})


class ContractValidationTests(unittest.TestCase):
    def test_operation_catalog_is_stable(self):
        self.assertEqual(tuple(OPERATIONS),
                         ("search", "get", "related",
                          "follow", "provenance", "inspect"))
        # The stable core node types must always remain part of the catalog;
        # the set may grow additively as the schema evolves.
        self.assertTrue({
            "concept", "technology", "entity", "procedure",
            "rule", "example", "dependency"}.issubset(VALID_NODE_TYPES))
        self.assertTrue(VALID_RELATIONSHIP_KINDS)

    def test_unknown_operation_rejected(self):
        with self.assertRaises(UnknownOperationError):
            validate_request({"operation": "drop", "arguments": {}})

    def test_validate_request_returns_strict_arguments(self):
        operation, args = validate_request(
            {"operation": "search",
             "arguments": {"query": "x", "limit": 10}})
        self.assertEqual(operation, "search")
        self.assertEqual(args, {"query": "x", "limit": 10})

        operation, args = validate_request(
            {"operation": "get", "arguments": {"node_id": "transport"}})
        self.assertEqual(args, {"node_id": "transport"})

    def test_validate_request_accepts_missing_arguments(self):
        operation, args = validate_request({"operation": "inspect"})
        self.assertEqual(operation, "inspect")
        self.assertEqual(args, {})

        operation, args = validate_request(
            {"operation": "inspect", "arguments": None})
        self.assertEqual(operation, "inspect")
        self.assertEqual(args, {})

    def test_validation_error_codes(self):
        tmp, db = _temp_seeded()
        try:
            interface = ToolInterface(db_path=db)
            try:
                def code(request):
                    return interface.execute(request)["error"]["code"]

                self.assertEqual(
                    code({"operation": "get", "arguments": {"node_id": ""}}),
                    "invalid_argument")
                self.assertEqual(
                    code({"operation": "get", "arguments": {"node_id": "  "}}),
                    "invalid_argument")
                self.assertEqual(
                    code({"operation": "get", "arguments": {"node_id": 5}}),
                    "invalid_argument")
                self.assertEqual(
                    code({"operation": "search",
                          "arguments": {"query": "x", "limit": 0}}),
                    "invalid_argument")
                self.assertEqual(
                    code({"operation": "search",
                          "arguments": {"query": "x", "limit": True}}),
                    "invalid_argument")
                self.assertEqual(
                    code({"operation": "search",
                          "arguments": {"query": "x", "limit": 2.5}}),
                    "invalid_argument")
                self.assertEqual(
                    code({"operation": "search",
                          "arguments": {"query": 1}}),
                    "invalid_argument")
                self.assertEqual(
                    code({"operation": "search",
                          "arguments": {"query": "x",
                                        "node_type": "entityx"}}),
                    "invalid_argument")
                self.assertEqual(
                    code({"operation": "follow",
                          "arguments": {"node_id": "transport",
                                        "relationship_type": "is_a"}}),
                    "invalid_relationship_type")
            finally:
                interface.close()
        finally:
            tmp.cleanup()

    def test_sql_path_command_keys_never_accepted(self):
        tmp, db = _temp_seeded()
        try:
            interface = ToolInterface(db_path=db)
            try:
                for op, kwargs in (
                    ("search", {"query": "x"}),
                    ("get", {"node_id": "transport"}),
                    ("related", {"node_id": "transport"}),
                    ("follow", {"node_id": "transport"}),
                    ("provenance", {"node_id": "transport"}),
                    ("inspect", {}),
                ):
                    for key in ("sql", "path", "command"):
                        arguments = dict(kwargs)
                        arguments[key] = "SELECT 1"
                        res = interface.execute(
                            {"operation": op, "arguments": arguments})
                        self.assertFalse(res["ok"], (op, key))
                        self.assertEqual(res["error"]["code"],
                                         "invalid_argument")
            finally:
                interface.close()
        finally:
            tmp.cleanup()

    def test_result_cap_bound(self):
        tmp, db = _temp_seeded(_seed_bulk_db)
        try:
            interface = ToolInterface(db_path=db)
            try:
                res = interface.execute({"operation": "search",
                                         "arguments": {"query": "bulk",
                                                       "limit": 300}})
                self.assertTrue(res["ok"], res)
                self.assertLessEqual(len(res["result"]), MAX_RESULTS)
                self.assertEqual(res["result"][0]["type"], "concept")

                res = interface.execute({"operation": "search",
                                         "arguments": {"query": "bulk"}})
                self.assertTrue(res["ok"], res)
                self.assertEqual(len(res["result"]), 20)
            finally:
                interface.close()
        finally:
            tmp.cleanup()


class RunnerTests(unittest.TestCase):
    def test_runner_file_success(self):
        tmp, db = _temp_seeded()
        try:
            request_file = os.path.join(tmp.name, "req.json")
            with open(request_file, "w", encoding="utf-8") as f:
                json.dump({"operation": "inspect"}, f)
            r = _run_runner(request_file, "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(r.stdout)
            self.assertTrue(data["ok"])
            self.assertEqual(data["operation"], "inspect")
            self.assertEqual(data["result"]["node_count"], 5)
        finally:
            tmp.cleanup()

    def test_runner_stdin(self):
        tmp, db = _temp_seeded(_seed_bulk_db)
        try:
            r = _run_runner("--db", db, stdin='{"operation": "inspect"}')
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(r.stdout)
            self.assertTrue(data["ok"])
            self.assertEqual(data["result"]["node_count"], 130)
        finally:
            tmp.cleanup()

    def test_runner_error_exit_code_is_one(self):
        tmp, db = _temp_seeded()
        try:
            r = _run_runner("--db", db,
                            stdin='{"operation": "get", "arguments": '
                                  '{"node_id": "nope"}}')
            self.assertEqual(r.returncode, 1, r.stdout)
            data = json.loads(r.stdout)
            self.assertFalse(data["ok"])
            self.assertEqual(data["operation"], "get")
            self.assertEqual(data["error"]["code"], "node_not_found")
        finally:
            tmp.cleanup()

    def test_runner_rejects_non_object_and_bad_json(self):
        tmp, db = _temp_seeded()
        try:
            for bad in ("[1,2,3]", "42", '"text"', "null",
                        '{"operation": "inspect"} garbage'):
                r = _run_runner("--db", db, stdin=bad)
                self.assertEqual(r.returncode, 1, bad)
                data = json.loads(r.stdout)
                self.assertFalse(data["ok"], bad)
                self.assertIn(data["error"]["code"],
                              ("invalid_request", "invalid_json"))
            r = _run_runner("--db", db, stdin='{"operation": "inspect"}')
            self.assertEqual(r.returncode, 0)
        finally:
            tmp.cleanup()

    def test_runner_missing_file(self):
        tmp = tempfile.TemporaryDirectory()
        try:
            missing = os.path.join(tmp.name, "does-not-exist.json")
            r = _run_runner(missing)
            self.assertEqual(r.returncode, 1)
            data = json.loads(r.stdout)
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"]["code"], "invalid_request")
        finally:
            tmp.cleanup()

    def test_runner_deep_request_does_not_touch_database(self):
        tmp, db = _temp_seeded()
        try:
            before = _sha256(db)
            for payload in ("[[[[[[1]]]]]]",
                            '{"operation": "search", "arguments": {"query": '
                            '"x", "sql": "nope"}}',
                            '{"operation": "delete", "arguments": {}}'):
                r = _run_runner("--db", db, stdin=payload)
                self.assertEqual(r.returncode, 1)
                data = json.loads(r.stdout)
                self.assertFalse(data["ok"])
                self.assertIn(data["error"]["code"],
                              ("invalid_request", "unknown_operation",
                               "invalid_argument"))
            self.assertEqual(_sha256(db), before)
        finally:
            tmp.cleanup()

    def test_runner_read_only_across_all_operations(self):
        tmp, db = _temp_seeded()
        try:
            before = _sha256(db)
            for payload in (
                '{"operation": "inspect"}',
                '{"operation": "get", "arguments": {"node_id": "transport"}}',
                '{"operation": "search", "arguments": {"query": "transport"}}',
                '{"operation": "related", "arguments": '
                '{"node_id": "transport"}}',
                '{"operation": "follow", "arguments": {"node_id": "transport",'
                ' "relationship_type": "extends"}}',
                '{"operation": "provenance", "arguments": '
                '{"node_id": "transport"}}',
            ):
                r = _run_runner("--db", db, stdin=payload)
                self.assertEqual(r.returncode, 0, (payload, r.stdout, r.stderr))
            self.assertEqual(_sha256(db), before)
        finally:
            tmp.cleanup()

    def test_module_execute_convenience(self):
        tmp, db = _temp_seeded()
        try:
            res = execute({"operation": "inspect"}, db_path=db)
            # convenience function closed its own connection
            res2 = execute({"operation": "inspect"}, db_path=db)
            self.assertTrue(res["ok"])
            self.assertTrue(res2["ok"])
            self.assertEqual(res["result"], res2["result"])
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()