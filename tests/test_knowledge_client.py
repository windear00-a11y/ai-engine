"""Integration tests for the Knowledge Engine Python Client SDK.

Scope
-----
The SDK (``knowledge_client``) is the thin, developer-facing Python client: it
builds contract-shaped requests, maps stable error codes onto clean exception
classes, and never touches SQLite, the repository, the schema, or the CLI.

What is verified here, per milestone:
* the SDK imports and exposes the documented API surface;
* the SDK package never imports/uses ``sqlite3``, ``retrieval.*``, the schema,
  or the CLI (static audit over the whole package);
* the client works against the real persistent session and in-process
  boundary, with deterministic, real results (no fabricated ids);
* structured error codes map to the matching SDK exception classes;
* transport failures (dead process, garbage responses) become clean
  ``TransportError`` / ``InvalidResponseError`` exceptions;
* the production database hash & integrity remain unchanged.
"""

import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import tokenize
import typing
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import knowledge_client  # noqa: E402
import knowledge_client.types as sdk_types  # noqa: E402
from knowledge_client import (  # noqa: E402
    InProcessTransport,
    InternalError,
    InvalidArgumentError,
    InvalidRelationshipTypeError,
    InvalidRequestError,
    InvalidResponseError,
    KnowledgeClient,
    KnowledgeClientError,
    KnowledgeError,
    NodeNotFoundError,
    OneShotTransport,
    SessionTransport,
    TransportError,
    TransportProtocol,
    UnknownOperationError,
)

from api.tools import ToolInterface  # noqa: E402  (public boundary, not SDK internals)
from retrieval.repository import KnowledgeRepository  # noqa: E402  (test fixture only)

PRODUCTION_DB = os.path.join(_ROOT, "database", "knowledge.db")
FORBIDDEN_TOKENS = (
    "sqlite", "sql", "repository", "retrieval", "knowledge_api",
    "KnowledgeStore", "ai_engine", "schema",
)
SDK_PACKAGE = os.path.join(_ROOT, "knowledge_client")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _seed_temp_db(db_path):
    """Small fixture DB with a real relationship to traverse."""
    repo = KnowledgeRepository(db_path)
    repo.initialize()
    sid = repo.add_source("fixture-source", version="1.0")
    repo.add_node("transport", "entity", "Transport(WriteTransport, ReadTransport)",
                  "An I/O transport with read and write halves", source_id=sid)
    repo.add_node("write-transport", "entity", "WriteTransport",
                  "Write side of a transport", source_id=sid)
    repo.add_node("read-transport", "entity", "ReadTransport",
                  "Read side of a transport", source_id=sid)
    repo.add_node("buffered-transport", "entity", "BufferedTransport",
                  "A buffered transport", source_id=sid)
    repo.add_node("log", "entity", "Usage", "Logging facilities", source_id=sid)
    repo.add_relationship("transport", "extends", "write-transport", "fixture")
    repo.add_relationship("transport", "extends", "read-transport", "fixture")
    repo.add_relationship("read-transport", "references", "log", "fixture")
    repo.close()


class SdkAuditTests(unittest.TestCase):
    """Static proof that the SDK stays out of engine internals."""

    ALLOWED_IMPORT_ROOTS = frozenset(
        list(sys.stdlib_module_names) + ["knowledge_client", "api"])

    def _package_files(self):
        return [
            os.path.join(SDK_PACKAGE, name)
            for name in sorted(os.listdir(SDK_PACKAGE))
            if name.endswith(".py")
        ]

    def _imports_of(self, path):
        with open(path, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports += [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split(".")[0])
        return imports

    def _code_only_source(self, path):
        with open(path, encoding="utf-8") as f:
            source = f.read()
        kept = []
        for t in tokenize.generate_tokens(io.StringIO(source).readline):
            if t.type in (tokenize.STRING, tokenize.COMMENT):
                kept.append(" " * (t.end[1] - t.start[1]))
            else:
                kept.append(t.string)
        return "".join(kept).lower()

    def _assert_no_forbidden_tokens(self, path):
        code = self._code_only_source(path)
        for token in FORBIDDEN_TOKENS:
            self.assertNotIn(
                token, code,
                "%s must not reference %r in code" % (os.path.basename(path), token))

    def test_sdk_imports_are_stdlib_plus_boundaries_only(self):
        for path in self._package_files():
            roots = set(self._imports_of(path))
            unknown = roots - self.ALLOWED_IMPORT_ROOTS
            self.assertFalse(
                {r for r in unknown if r not in {"api"}},
                "%s imports disallowed modules: %s"
                % (os.path.basename(path), unknown))

    def test_sdk_never_imports_sqlite_retrieval_schema_cli(self):
        for path in self._package_files():
            roots = set(self._imports_of(path))
            for banned in ("sqlite3", "retrieval", "schema", "ai_engine"):
                self.assertNotIn(
                    banned, roots,
                    "%s must not import %r" % (os.path.basename(path), banned))

    def test_sdk_source_has_no_forbidden_access(self):
        for path in self._package_files():
            self._assert_no_forbidden_tokens(path)

    def test_sdk_exports_documented_surface(self):
        for name in ("KnowledgeClient", "SessionTransport", "InProcessTransport",
                     "OneShotTransport", "TransportProtocol",
                     "KnowledgeError", "NodeNotFoundError",
                     "InvalidRequestError", "UnknownOperationError",
                     "InvalidArgumentError", "InvalidRelationshipTypeError",
                     "InternalError", "TransportError", "InvalidResponseError"):
            self.assertTrue(hasattr(knowledge_client, name), "missing %s" % name)
        self.assertTrue(isinstance(knowledge_client.__version__, str))

    def test_typed_methods_have_annotations(self):
        hints = typing.get_type_hints(KnowledgeClient.search)
        self.assertIn("query", hints)
        self.assertIn("limit", hints)
        hints = typing.get_type_hints(KnowledgeClient.get)
        self.assertIn("node_id", hints)
        self.assertEqual(typing.get_type_hints(KnowledgeClient.inspect)["return"],
                         sdk_types.InspectStats)

    def test_client_rejects_non_transport(self):
        with self.assertRaises(TypeError):
            KnowledgeClient(42)


class InProcessSdkTests(unittest.TestCase):
    """The SDK client against the in-process public boundary on production."""

    @classmethod
    def setUpClass(cls):
        cls._interface = ToolInterface(db_path=PRODUCTION_DB)
        cls.client = KnowledgeClient(InProcessTransport(interface=cls._interface))
        cls.hash_before = _sha256(PRODUCTION_DB)

    @classmethod
    def tearDownClass(cls):
        cls._interface.close()

    def test_inspect_reports_real_counts(self):
        result = self.client.inspect()
        self.assertGreaterEqual(result["source_count"], 6)
        self.assertGreaterEqual(result["node_count"], 4846)
        self.assertGreaterEqual(result["relationship_count"], 55)
        self.assertEqual(
            sum(result["nodes_by_type"].values()), result["node_count"])
        self.assertEqual(
            sum(result["relationships_by_type"].values()),
            result["relationship_count"])

    def test_search_get_provenance_chain(self):
        hits = self.client.search("exception", limit=3)
        self.assertEqual(len(hits), 3)
        for hit in hits:
            self.assertIn("id", hit)
        node = self.client.get(hits[0]["id"])
        self.assertEqual(node["id"], hits[0]["id"])
        prov = self.client.provenance(node["id"])
        self.assertEqual(prov["node_id"], node["id"])
        self.assertIn("source_name", prov)

    def test_search_related(self):
        hits = self.client.search("transport", node_type="entity", limit=20)
        self.assertTrue(hits)
        neighbours = self.client.related(hits[0]["id"])
        self.assertIsInstance(neighbours, list)
        for entry in neighbours:
            self.assertIn("node", entry)
            self.assertIn("via", entry)

    def test_deterministic_results(self):
        first = self.client.search("exception", limit=4)
        second = self.client.search("exception", limit=4)
        self.assertEqual(first, second)
        node_id = first[0]["id"]
        self.assertEqual(self.client.get(node_id), self.client.get(node_id))
        self.assertEqual(self.client.inspect(), self.client.inspect())

    def test_structured_error_mapping(self):
        with self.assertRaises(NodeNotFoundError) as ctx:
            self.client.get("id-that-does-not-exist")
        self.assertEqual(ctx.exception.code, "node_not_found")
        self.assertEqual(ctx.exception.node_id, "id-that-does-not-exist")

        with self.assertRaises(UnknownOperationError) as ctx:
            self.client.request("drop")
        self.assertEqual(ctx.exception.code, "unknown_operation")

        with self.assertRaises(InvalidArgumentError) as ctx:
            self.client.search("x", limit=-5)
        self.assertEqual(ctx.exception.code, "invalid_argument")

        with self.assertRaises(InvalidRelationshipTypeError) as ctx:
            self.client.follow("exceptions", relationship_type="is-a-kid-of")
        self.assertEqual(ctx.exception.code, "invalid_relationship_type")

    def test_errors_are_subclasses_of_knowledge_error(self):
        for exc_instance in (
                NodeNotFoundError("id-that-does-not-exist"),
                InvalidArgumentError("boom"),
                UnknownOperationError("drop"),
                InvalidRequestError("bad"),
                InvalidRelationshipTypeError("bad"),
                InternalError("internal")):
            self.assertIsInstance(exc_instance, KnowledgeError)
            self.assertIsInstance(exc_instance, KnowledgeClientError)

    def test_production_database_hash_unchanged(self):
        self.assertEqual(_sha256(PRODUCTION_DB), self.hash_before)

    def test_production_database_integrity_clean(self):
        import sqlite3  # read-only verifier for the milestone report
        conn = sqlite3.connect(PRODUCTION_DB)
        try:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0],
                             "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(),
                             [])
        finally:
            conn.close()


class InProcessFixtureSdkTests(unittest.TestCase):
    """SDK on a small temp DB: full traversal, deterministic, read-only."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "knowledge.db")
        _seed_temp_db(self.db)
        self.hash_before = _sha256(self.db)
        self.interface = ToolInterface(db_path=self.db)
        self.client = KnowledgeClient(InProcessTransport(interface=self.interface))

    def tearDown(self):
        self.interface.close()
        self.tmp.cleanup()

    def test_inspect_search_get_provenance_chain(self):
        result = self.client.inspect()
        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["node_count"], 5)
        hits = self.client.search("ReadTransport", limit=5)
        ids = {h["id"] for h in hits}
        self.assertIn("read-transport", ids)
        node = self.client.get("read-transport")
        self.assertEqual(node["name"], "ReadTransport")
        prov = self.client.provenance("read-transport")
        self.assertEqual(prov["node_id"], "read-transport")

    def test_search_related(self):
        hits = self.client.search("transport", limit=10)
        source_id = next(h["id"] for h in hits if h["id"] == "transport")
        neighbours = self.client.related(source_id)
        self.assertGreaterEqual(len(neighbours), 1)
        via_types = {v["relationship_type"]
                     for entry in neighbours for v in entry["via"]}
        self.assertIn("extends", via_types)

    def test_follow_traversal(self):
        hits = self.client.search("transport", limit=10)
        found = None
        for hit in hits:
            edges = self.client.follow(hit["id"], relationship_type="extends")
            if edges:
                found = (hit["id"], edges)
                break
        self.assertIsNotNone(
            found, "no discovered node has an 'extends' edge to follow")
        _, edges = found or ("", [])
        self.assertEqual({e["target_node_id"] for e in edges},
                         {"write-transport", "read-transport"})

    def test_deterministic_repeated_requests(self):
        first = self.client.search("usage", limit=5)
        second = self.client.search("usage", limit=5)
        self.assertEqual(first, second)

    def test_temp_database_bytes_unchanged(self):
        self.client.inspect()
        self.client.search("transport", limit=5)
        self.client.follow("transport", relationship_type="extends")
        self.client.provenance("transport")
        self.assertEqual(_sha256(self.db), self.hash_before)


class SessionSdkTests(unittest.TestCase):
    """The SDK against the real persistent session process on a temp DB."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "knowledge.db")
        _seed_temp_db(self.db)
        self.hash_before = _sha256(self.db)
        self.session = SessionTransport(db=self.db)
        self.client = KnowledgeClient(self.session)

    def tearDown(self):
        self.session.close()
        self.tmp.cleanup()

    def test_search_get_provenance_chain(self):
        hits = self.client.search("ReadTransport", limit=5)
        ids = {h["id"] for h in hits}
        self.assertIn("read-transport", ids)
        node = self.client.get("read-transport")
        self.assertEqual(node["name"], "ReadTransport")
        prov = self.client.provenance("read-transport")
        self.assertEqual(prov["node_id"], "read-transport")

    def test_search_related_follow(self):
        hits = self.client.search("transport", limit=10)
        source_id = next(h["id"] for h in hits if h["id"] == "transport")
        neighbours = self.client.related(source_id)
        self.assertGreaterEqual(len(neighbours), 1)
        edges = self.client.follow(source_id, relationship_type="extends")
        self.assertEqual({e["target_node_id"] for e in edges},
                         {"write-transport", "read-transport"})
        for edge in edges:
            neighbour = self.client.get(edge["target_node_id"])
            self.assertEqual(neighbour["id"], edge["target_node_id"])

    def test_invalid_then_valid_continues(self):
        with self.assertRaises(NodeNotFoundError) as ctx:
            self.client.get("missing-node")
        self.assertEqual(ctx.exception.code, "node_not_found")
        ok = self.client.inspect()
        self.assertEqual(ok["node_count"], 5)

    def test_error_does_not_kill_process(self):
        bad = self.client.execute({"operation": "get",
                                   "arguments": {"node_id": 7}})
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"]["code"], "invalid_argument")
        good = self.client.inspect()
        self.assertEqual(good["node_count"], 5)

    def test_repository_initialized_once(self):
        for _ in range(4):
            self.assertTrue(self.client.inspect())
        self.session.close()
        stats = self.session.session_stats()
        self.assertIsNotNone(stats)
        assert stats is not None
        self.assertEqual(stats["initializations"], 1)
        self.assertEqual(stats["requests"], 4)

    def test_eof_terminates_cleanly(self):
        self.client.inspect()
        self.session.close()  # EOF on stdin
        detail = self.session.session_stats()
        self.assertIsNotNone(detail)

    def test_deterministic_across_requests(self):
        first = self.client.search("transport", limit=5)
        second = self.client.search("transport", limit=5)
        self.assertEqual(first, second)

    def test_temp_database_bytes_unchanged(self):
        self.client.inspect()
        self.client.search("transport", limit=5)
        self.client.follow("transport", relationship_type="extends")
        self.assertEqual(_sha256(self.db), self.hash_before)


class OneShotSdkTests(unittest.TestCase):
    """The SDK over the one-shot runner (fresh interpreter per request)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "knowledge.db")
        _seed_temp_db(self.db)
        self.client = KnowledgeClient(OneShotTransport(db=self.db))

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def test_inspect_search_get_chain(self):
        result = self.client.inspect()
        self.assertEqual(result["node_count"], 5)
        hits = self.client.search("ReadTransport", limit=5)
        ids = {h["id"] for h in hits}
        self.assertIn("read-transport", ids)
        node = self.client.get("read-transport")
        self.assertEqual(node["name"], "ReadTransport")

    def test_error_paths(self):
        with self.assertRaises(UnknownOperationError):
            self.client.request("does-not-exist")
        with self.assertRaises(NodeNotFoundError):
            self.client.get("missing-node")
        with self.assertRaises(InvalidArgumentError):
            self.client.search("x", node_type="not-a-type")


class SdkTransportFailureTests(unittest.TestCase):
    """Clean exceptions instead of raw tracebacks when transport misbehaves."""

    def test_transport_raising_oserror_becomes_transport_error(self):
        class BrokenTransport:
            def execute(self, request):
                raise OSError("pipe broken")
            def close(self):
                pass

        client = KnowledgeClient(BrokenTransport())
        with self.assertRaises(TransportError) as ctx:
            client.inspect()
        self.assertTrue(ctx.exception.code.startswith("client:"))
        self.assertIn("pipe broken", ctx.exception.message)

    def test_transport_returning_garbage_becomes_invalid_response(self):
        class GarbageTransport:
            def execute(self, request):
                return "not a dictionary"
            def close(self):
                pass

        client = KnowledgeClient(GarbageTransport())
        with self.assertRaises(InvalidResponseError):
            client.inspect()

    def test_transport_ok_false_with_bad_error_becomes_invalid_response(self):
        class BadErrorTransport:
            def execute(self, request):
                return {"ok": False, "operation": "search", "error": "oops"}
            def close(self):
                pass

        client = KnowledgeClient(BadErrorTransport())
        with self.assertRaises(InvalidResponseError):
            client.search("anything")

    def test_session_use_after_close_is_transport_error(self):
        transport = SessionTransport()
        try:
            client = KnowledgeClient(transport)
            client.inspect()
        finally:
            transport.close()
        with self.assertRaises(TransportError):
            client.inspect()

    def test_session_dead_process_becomes_transport_error(self):
        # Use a fast-failing shim for 'python -m api.session' so the session
        # dies immediately instead of spending 20-30s importing the engine
        # before failing on startup.
        shim_dir = tempfile.mkdtemp()
        api_dir = os.path.join(shim_dir, "api")
        os.makedirs(api_dir)
        with open(os.path.join(api_dir, "__init__.py"), "w", encoding="utf-8"):
            pass
        with open(os.path.join(api_dir, "session.py"), "w", encoding="utf-8") as f:
            f.write("import sys\nsys.stderr.write('boom\\n')\n"
                    "sys.stderr.flush()\nsys.exit(3)\n")
        transport = None
        try:
            transport = SessionTransport(
                db=os.path.join(_ROOT, "does-not-exist.db"),
                python=sys.executable, root=shim_dir, wait_timeout=5)
            client = KnowledgeClient(transport)
            with self.assertRaises(TransportError):
                client.inspect()
        finally:
            if transport is not None:
                transport.close()
            import shutil
            shutil.rmtree(shim_dir, ignore_errors=True)

    def test_one_shot_no_output_is_invalid_response(self):
        class NoOutputTransport:
            def execute(self, request):
                return {"ok": True}  # missing 'result'
            def close(self):
                pass

        client = KnowledgeClient(NoOutputTransport())
        with self.assertRaises(InvalidResponseError):
            client.inspect()


class SdkProductionIsolationTests(unittest.TestCase):
    """SDK activity must not alter the production database."""

    HASH_BEFORE = None

    @classmethod
    def setUpClass(cls):
        cls.HASH_BEFORE = _sha256(PRODUCTION_DB)

    def test_production_session_read_only(self):
        before = _sha256(PRODUCTION_DB)
        transport = SessionTransport(wait_timeout=180)
        client = KnowledgeClient(transport)
        try:
            result = client.inspect()
            self.assertGreaterEqual(result["node_count"], 4846)
            node = client.get("exceptions")
            self.assertEqual(node["id"], "exceptions")
        finally:
            client.close()
        stats = transport.session_stats()
        self.assertIsNotNone(stats)
        assert stats is not None
        self.assertEqual(stats["initializations"], 1)
        self.assertEqual(_sha256(PRODUCTION_DB), before)

    def test_production_hash_still_unchanged(self):
        self.assertEqual(_sha256(PRODUCTION_DB), self.HASH_BEFORE)


if __name__ == "__main__":
    unittest.main()