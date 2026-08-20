"""Integration tests for the independent external client.

Purpose
-------
Prove that an independent program can consume the Knowledge Engine through
the public tool-call contract without opening SQLite, without reaching into
``retrieval`` internals or the schema, and without depending on the CLI.

The client under test (``client.py``) is contract-only: it is injected a
``send`` callable and never imports ``sqlite3``, ``retrieval``, the schema,
or the CLI. For the strongest form of independence the same client is also
run against the real process boundary (``python -m api.tools -`` reading one
JSON request from stdin).

Isolation guarantees enforced here
----------------------------------
* static audit: ``client.py`` imports only stdlib modules;
* the client never touches ``database/knowledge.db`` itself -- integrity and
  the byte hash of the production database are verified unchanged around all
  of its activity;
* every operation is exercised through the approved six-operation contract.
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
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import client  # noqa: E402
from client import (  # noqa: E402
    ContractError,
    KnowledgeClient,
    SessionTransport,
    SubprocessTransport,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.tools import ToolInterface  # public boundary injected into the client
from retrieval.repository import (
    KnowledgeRepository,  # fixture-builder only; never used by client.py
)

PRODUCTION_DB = os.path.join(_ROOT, "database", "knowledge.db")
FORBIDDEN_TOKENS = (
    "sqlite", "sql", "repository", "retrieval", "knowledge_api",
    "KnowledgeStore", "ai_engine", "schema",
)


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


class ContractAuditTests(unittest.TestCase):
    """Static proof that the independent client stays inside the contract."""

    CLIENT_PATH = os.path.join(_HERE, "client.py")

    def _imports(self):
        with open(self.CLIENT_PATH, encoding="utf-8") as f:
            tree = ast.parse(f.read())
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports += [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module.split(".")[0])
        return imports

    def _code_only_source(self):
        """Return the module source with docstrings, strings and comments removed.

        The audit must prove the client never *uses* forbidden machinery, not
        that its own documentation never mentions the word -- so documentation
        strings are stripped before scanning.
        """
        with open(self.CLIENT_PATH, encoding="utf-8") as f:
            source = f.read()
        kept = []
        for t in tokenize.generate_tokens(io.StringIO(source).readline):
            if t.type in (tokenize.STRING, tokenize.COMMENT):
                kept.append(" " * (t.end[1] - t.start[1]))
            else:
                kept.append(t.string)
        return "".join(kept).lower()

    def test_client_imports_are_stdlib_only(self):
        imports = set(self._imports())
        self.assertTrue(
            imports <= client.STDLIB_ONLY_IMPORTS.union({"unittest"}),
            "client imports non-stdlib modules: %s" % (imports - client.STDLIB_ONLY_IMPORTS))

    def test_client_source_has_no_forbidden_access(self):
        code = self._code_only_source()
        for token in FORBIDDEN_TOKENS:
            self.assertNotIn(token, code,
                             "client.py code must not reference %r" % token)


class InProcessIntegrationTests(unittest.TestCase):
    """The client against the in-process public ToolInterface on production."""

    @classmethod
    def setUpClass(cls):
        cls._interface = ToolInterface(db_path=PRODUCTION_DB)
        cls.engine = KnowledgeClient(cls._interface.execute)
        cls.hash_before = _sha256(PRODUCTION_DB)

    @classmethod
    def tearDownClass(cls):
        cls._interface.close()

    def test_inspect_reports_real_counts(self):
        result = self.engine.inspect()
        self.assertGreaterEqual(result["source_count"], 6)
        self.assertGreaterEqual(result["node_count"], 4846)
        self.assertGreaterEqual(result["relationship_count"], 55)
        by_type = result["nodes_by_type"]
        self.assertEqual(sum(by_type.values()), result["node_count"])
        by_rel = result["relationships_by_type"]
        self.assertEqual(sum(by_rel.values()), result["relationship_count"])

    def test_search_get_chain(self):
        hits = self.engine.search("exception", limit=3)
        self.assertEqual(len(hits), 3)
        for hit in hits:
            self.assertIn("id", hit)
            self.assertIn("type", hit)
            self.assertIn("name", hit)
        node = self.engine.get(hits[0]["id"])
        self.assertEqual(node["id"], hits[0]["id"])
        self.assertEqual(node["type"], hits[0]["type"])
        self.assertEqual(node["name"], hits[0]["name"])

    def test_get_provenance_chain(self):
        node = self.engine.get("exceptions")
        self.assertEqual(node["id"], "exceptions")
        prov = self.engine.provenance("exceptions")
        self.assertEqual(prov["node_id"], "exceptions")
        self.assertIsInstance(prov["source_id"], int)
        self.assertTrue(prov["source_name"])
        self.assertIn("imported_at", prov)

    def test_related_returns_neighbours(self):
        node = self.engine.get("exceptions")
        self.assertEqual(node["id"], "exceptions")
        neighbours = self.engine.related("exceptions")
        self.assertGreaterEqual(len(neighbours), 1)
        for entry in neighbours:
            self.assertIn("node", entry)
            self.assertIn("id", entry["node"])
            self.assertIsInstance(entry["via"], list)
            for via in entry["via"]:
                self.assertIn("direction", via)
                self.assertIn("relationship_type", via)

    def test_real_relationship_traversal(self):
        """Dynamically discover production relationships and traverse them."""
        rel_by_type = self.engine.inspect()["relationships_by_type"]
        self.assertTrue(rel_by_type)
        reltype, count = max(rel_by_type.items(), key=lambda kv: kv[1])
        self.assertGreaterEqual(count, 1)

        # discover candidate nodes by search (no hard-coded ids)
        candidates = []
        for query in ("transport", "Transport"):
            hits = self.engine.search(query, node_type="entity", limit=20)
            candidates.extend(hits or [])
        seen, nodes = set(), []
        for hit in candidates:
            if hit["id"] not in seen:
                seen.add(hit["id"])
                nodes.append(hit)

        found = None
        for probe in nodes[:10]:
            edges = self.engine.follow(probe["id"], relationship_type=reltype)
            if edges:
                found = (probe["id"], edges)
                break
        self.assertIsNotNone(
            found,
            "no dynamically discovered node has %r edges" % reltype)

        source_id, edges = found or ("", [])
        self.assertTrue(source_id)
        for edge in edges:
            self.assertIn("target_node_id", edge)
            self.assertEqual(edge["relationship_type"], reltype)
            self.assertEqual(edge["node"]["id"], edge["target_node_id"])
        neighbour = self.engine.get(edges[0]["target_node_id"])
        self.assertEqual(neighbour["id"], edges[0]["target_node_id"])
        self.assertNotEqual(neighbour["id"], source_id)

    def test_deterministic_results(self):
        first = self.engine.search("exception", limit=4)
        second = self.engine.search("exception", limit=4)
        self.assertEqual(first, second)
        node_id = first[0]["id"]
        self.assertEqual(self.engine.get(node_id), self.engine.get(node_id))
        self.assertEqual(self.engine.inspect(), self.engine.inspect())

    def test_structured_error_handling(self):
        with self.assertRaises(ContractError) as ctx:
            self.engine.get("id-that-does-not-exist")
        self.assertEqual(ctx.exception.code, "node_not_found")
        self.assertIn("id-that-does-not-exist", ctx.exception.message)

    def test_unknown_operation_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            self.engine.request("drop")
        self.assertEqual(ctx.exception.code, "unknown_operation")

    def test_invalid_arguments_rejected(self):
        with self.assertRaises(ContractError) as ctx:
            self.engine.raw_request("search", {"query": 1})
        self.assertEqual(ctx.exception.code, "invalid_argument")
        with self.assertRaises(ContractError) as ctx:
            self.engine.raw_request("search", {"query": "x", "sql": "SELECT 1"})
        self.assertEqual(ctx.exception.code, "invalid_argument")

    def test_engine_rejects_non_object_request(self):
        response = self._interface.execute("not a request object")
        self.assertFalse(response["ok"])
        self.assertEqual(response["error"]["code"], "invalid_request")

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


class ProcessBoundaryTests(unittest.TestCase):
    """The same client across a real OS process boundary via ``api.tools``.

    Each call below spawns a fresh interpreter, feeds one JSON request on
    stdin, and parses exactly one JSON response -- proving the contract works
    outside the Python API call stack of the test runner.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "knowledge.db")
        _seed_temp_db(self.db)
        self.hash_before = _sha256(self.db)
        self.engine = KnowledgeClient(SubprocessTransport(db=self.db))

    def tearDown(self):
        self.tmp.cleanup()

    def _direct_runner(self, input_text, db=None):
        """Raw invocations of the runner (bypasses the client transport)."""
        cmd = [sys.executable, "-m", "api.tools", "-"]
        if db is not None:
            cmd.extend(["--db", db])
        return subprocess.run(cmd, cwd=_ROOT, input=input_text,
                              capture_output=True, text=True, timeout=180)

    def test_inspect_search_get_chain_across_processes(self):
        result = self.engine.inspect()
        self.assertEqual(result["source_count"], 1)
        self.assertEqual(result["node_count"], 5)
        hits = self.engine.search("ReadTransport", limit=5)
        self.assertTrue(hits)
        ids = {h["id"] for h in hits}
        self.assertIn("read-transport", ids)
        node = self.engine.get("read-transport")
        self.assertEqual(node["name"], "ReadTransport")

    def test_follow_traversal_across_processes(self):
        hits = self.engine.search("transport", limit=10)
        found = None
        for hit in hits:
            edges = self.engine.follow(hit["id"], relationship_type="extends")
            if edges:
                found = (hit["id"], edges)
                break
        self.assertIsNotNone(
            found, "no discovered node has an 'extends' edge to follow")
        _, edges = found or ("", [])
        self.assertTrue(edges)
        self.assertEqual({e["target_node_id"] for e in edges},
                         {"write-transport", "read-transport"})

    def test_error_paths_across_processes(self):
        with self.assertRaises(ContractError) as ctx:
            self.engine.get("missing-node")
        self.assertEqual(ctx.exception.code, "node_not_found")
        with self.assertRaises(ContractError) as ctx:
            self.engine.raw_request("does-not-exist")
        self.assertEqual(ctx.exception.code, "unknown_operation")
        with self.assertRaises(ContractError) as ctx:
            self.engine.raw_request("search", {"query": "x", "command": "rm"})
        self.assertEqual(ctx.exception.code, "invalid_argument")

    def test_exactly_one_json_document_per_response(self):
        proc = self._direct_runner('{"operation": "inspect"}', db=self.db)
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("Traceback", proc.stderr)
        stripped = proc.stdout.strip()
        payload, end = json.JSONDecoder().raw_decode(stripped)
        self.assertEqual(stripped[end:].strip(), "")
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["result"]["node_count"], 5)

    def test_invalid_json_gets_structured_error(self):
        proc = self._direct_runner("{ not valid json", db=self.db)
        self.assertEqual(proc.returncode, 1)
        payload, _ = json.JSONDecoder().raw_decode(proc.stdout.strip())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_deterministic_across_processes(self):
        first = self.engine.search("transport", limit=5)
        second = KnowledgeClient(SubprocessTransport(db=self.db)) \
            .search("transport", limit=5)
        self.assertEqual(first, second)

    def test_temp_database_bytes_unchanged(self):
        self.engine.inspect()
        self.engine.search("transport", limit=5)
        self.engine.follow("transport", relationship_type="extends")
        self.assertEqual(_sha256(self.db), self.hash_before)

    def test_production_inspect_via_own_process(self):
        """One production request through an entirely separate interpreter."""
        prod = KnowledgeClient(SubprocessTransport())
        before = _sha256(PRODUCTION_DB)
        result = prod.inspect()
        self.assertGreaterEqual(result["node_count"], 4846)
        self.assertEqual(_sha256(PRODUCTION_DB), before)


class SessionIntegrationTests(unittest.TestCase):
    """The independent client against the persistent session process.

    The same contract-only client talks to ``python -m api.session`` through
    :class:`client.SessionTransport` (one JSON request per line in, one JSON
    response per line out, EOF shutdown).
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "knowledge.db")
        _seed_temp_db(self.db)
        self.engine = KnowledgeClient(SessionTransport(db=self.db))

    def tearDown(self):
        self.engine._send.close()
        self.tmp.cleanup()

    def test_session_inspect_search_get_chain(self):
        result = self.engine.inspect()
        self.assertEqual(result["node_count"], 5)
        hits = self.engine.search("ReadTransport", limit=5)
        ids = {h["id"] for h in hits}
        self.assertIn("read-transport", ids)
        node = self.engine.get("read-transport")
        self.assertEqual(node["name"], "ReadTransport")

    def test_session_all_six_operations(self):
        self.assertTrue(self.engine.inspect())
        hits = self.engine.search("transport", limit=10)
        source_id = next(h["id"] for h in hits if h["id"] == "transport")
        node = self.engine.get(source_id)
        self.assertEqual(node["id"], source_id)
        prov = self.engine.provenance(source_id)
        self.assertEqual(prov["node_id"], source_id)
        neighbours = self.engine.related(source_id)
        self.assertGreaterEqual(len(neighbours), 1)
        edges = self.engine.follow(source_id, relationship_type="extends")
        self.assertEqual({e["target_node_id"] for e in edges},
                         {"write-transport", "read-transport"})

    def test_session_invalid_then_valid(self):
        with self.assertRaises(ContractError) as ctx:
            self.engine.get("missing-node")
        self.assertEqual(ctx.exception.code, "node_not_found")
        ok = self.engine.inspect()
        self.assertTrue(ok)

    def test_session_error_does_not_kill_process(self):
        bad = self.engine._send({"operation": "get",
                                 "arguments": {"node_id": 7}})
        self.assertFalse(bad["ok"])
        self.assertEqual(bad["error"]["code"], "invalid_argument")
        good = self.engine.inspect()
        self.assertEqual(good["node_count"], 5)

    def test_session_repository_initialized_once(self):
        for _ in range(4):
            self.assertTrue(self.engine.inspect())
        self.engine._send.close()
        stats = self.engine._send.session_stats()
        self.assertIsNotNone(stats)
        assert stats is not None
        self.assertEqual(stats["initializations"], 1)
        self.assertEqual(stats["requests"], 4)

    def test_session_eof_terminates_cleanly(self):
        self.engine.inspect()
        self.engine._send.close()  # EOF on stdin
        detail = self.engine._send.session_stats()
        self.assertIsNotNone(detail)

    def test_production_session_read_only(self):
        before = _sha256(PRODUCTION_DB)
        prod = KnowledgeClient(SessionTransport(wait_timeout=180))
        try:
            result = prod.inspect()
            self.assertEqual(result["node_count"], 4846)
            node = prod.get("exceptions")
            self.assertEqual(node["id"], "exceptions")
        finally:
            prod._send.close()
        stats = prod._send.session_stats()
        self.assertIsNotNone(stats)
        assert stats is not None
        self.assertEqual(stats["initializations"], 1)
        self.assertEqual(_sha256(PRODUCTION_DB), before)


if __name__ == "__main__":
    unittest.main()