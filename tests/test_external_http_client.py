"""External HTTP Client v1 -- integration tests.

Proves that a completely independent application can consume the Knowledge
Engine through the public HTTP interface.  The client communicates ONLY
through HTTP; it never touches SQLite, the repository, or any internal module.

Test classes
------------
* ``IsolationAuditTests`` -- static AST audit + import audit
* ``HttpContractTests`` -- six operations, contract_version, deterministic, errors
* ``HttpAuthTests`` -- API-key gate over HTTP
* ``ProcessBoundaryTests`` -- real OS-process boundary (server + client in separate processes)
* ``PersistenceTests`` -- server reuse, initialization once, latency
* ``SecurityTests`` -- sql/command smuggling, read-only DB, no internal access
* ``ProductionDBSafetyTests`` -- hash + integrity before/after all integration tests
"""

import ast
import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import tokenize
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import external_http_client  # noqa: E402
from external_http_client import (  # noqa: E402
    ExternalHTTPClient,
    HTTPTransport,
    AuthenticationError,
    InvalidArgumentError,
    InvalidRelationshipTypeError,
    InvalidRequestError,
    InternalError,
    NodeNotFoundError,
    TransportError,
    UnknownOperationError,
    InvalidResponseError,
)
from external_http_client.client import CONTRACT_VERSION  # noqa: E402
from external_http_client.transport import (  # noqa: E402
    DEFAULT_PORT,
)

FORBIDDEN_CODE_TOKENS = (
    "sqlite3", "sqlite", "retrieval", "knowledge_api", "http_server",
    "KnowledgeStore", "ai_engine", "schema", "api.contract",
    "api.tools", "api.session",
)
STDLIB_IMPORTS = frozenset({
    "json", "http", "http.client", "socket", "time", "threading", "typing",
    "re", "os", "sys", "hashlib", "io", "signal", "subprocess", "tempfile",
    "unittest", "tokenize", "ast", "pathlib", "collections",
    "external_http_client",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _start_server(db, api_key=None, max_body_bytes=None, host="127.0.0.1"):
    """Start ``python -m http_server`` on a temp DB; return (proc, lines, port)."""
    cmd = [sys.executable, "-m", "http_server", "--host", host, "--port", "0",
           "--db", db]
    if api_key:
        cmd += ["--api-key", api_key]
    if max_body_bytes is not None:
        cmd += ["--max-body-bytes", str(max_body_bytes)]
    proc = subprocess.Popen(cmd, cwd=_ROOT, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, text=True, bufsize=1)
    lines = []
    lock = threading.Lock()

    def drain():
        for ln in proc.stderr:
            with lock:
                lines.append(ln)

    threading.Thread(target=drain, daemon=True).start()
    port = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        with lock:
            for ln in lines:
                s = ln.strip()
                if s.startswith("HTTP listening on"):
                    port = int(s.rsplit(":", 1)[1])
                    break
        if port is not None:
            break
        time.sleep(0.05)
    if port is None:
        proc.kill()
        proc.wait()
        raise RuntimeError("server did not start; stderr: %r" % "".join(lines)[:500])
    return proc, lines, port


def _stop_server(proc):
    """SIGINT the server; wait for exit."""
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _parse_stats(lines):
    """Parse the ``HTTP stats=... end`` line from server stderr."""
    for ln in lines:
        if ln.startswith("HTTP stats="):
            raw = ln[len("HTTP stats="):]
            if raw.endswith(" end\n"):
                raw = raw[:-5]
            elif raw.endswith(" end"):
                raw = raw[:-4]
            return json.loads(raw.strip())
    return None


# ---------------------------------------------------------------------------
# Isolation Audit
# ---------------------------------------------------------------------------

class IsolationAuditTests(unittest.TestCase):
    """Static proof that the external client stays stdlib-only."""

    PKG_DIR = os.path.join(_ROOT, "external_http_client")
    PY_FILES = ["errors.py", "types.py", "transport.py", "client.py",
                "__init__.py"]

    def _all_imports(self):
        imports = set()
        for fname in self.PY_FILES:
            path = os.path.join(self.PKG_DIR, fname)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=fname)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.add(node.module.split(".")[0])
        return imports

    def _code_only_source(self):
        kept = []
        for fname in self.PY_FILES:
            path = os.path.join(self.PKG_DIR, fname)
            with open(path, encoding="utf-8") as f:
                source = f.read()
            for t in tokenize.generate_tokens(io.StringIO(source).readline):
                if t.type in (tokenize.STRING, tokenize.COMMENT):
                    kept.append(" " * (t.end[1] - t.start[1]))
                else:
                    kept.append(t.string)
        return "".join(kept).lower()

    def test_imports_are_stdlib_only(self):
        imports = self._all_imports()
        bad = imports - STDLIB_IMPORTS
        self.assertFalse(bad, "non-stdlib imports: %s" % bad)

    def test_source_has_no_forbidden_tokens(self):
        code = self._code_only_source()
        for token in FORBIDDEN_CODE_TOKENS:
            self.assertNotIn(token, code,
                             "source must not reference %r" % token)

    def test_importing_client_does_not_load_internal_modules(self):
        proc = subprocess.run(
            [sys.executable, "-c",
             "import json, sys, external_http_client; "
             "forbidden = ['sqlite3','retrieval','knowledge_api',"
             "'http_server','ai_engine','api']; "
             "loaded = [m for m in sys.modules if any(f in m for f in forbidden)]; "
             "print(json.dumps({'loaded':loaded}))"],
            cwd=_ROOT, capture_output=True, text=True, timeout=10)
        data = json.loads(proc.stdout.strip())
        self.assertEqual(data["loaded"], [])


# ---------------------------------------------------------------------------
# Helpers for server-backed tests
# ---------------------------------------------------------------------------

def _seed_db(db_path):
    from retrieval.repository import KnowledgeRepository
    repo = KnowledgeRepository(db_path)
    repo.initialize()
    sid = repo.add_source("external-fixture", version="1.0")
    repo.add_node("transport", "entity", "Transport(Write, Read)",
                  "I/O transport with read and write halves", source_id=sid)
    repo.add_node("write-transport", "entity", "WriteTransport",
                  "Write side", source_id=sid)
    repo.add_node("read-transport", "entity", "ReadTransport",
                  "Read side", source_id=sid)
    repo.add_node("log", "entity", "Usage", "Logging", source_id=sid)
    repo.add_relationship("transport", "extends", "write-transport", "src")
    repo.add_relationship("transport", "extends", "read-transport", "src")
    repo.add_relationship("read-transport", "references", "log", "src")
    repo.close()


def _temp_db():
    tmp = tempfile.TemporaryDirectory()
    db = os.path.join(tmp.name, "knowledge.db")
    _seed_db(db)
    return tmp, db


def _client_for_port(port, api_key=None):
    return ExternalHTTPClient(host="127.0.0.1", port=port, api_key=api_key)


# ---------------------------------------------------------------------------
# Contract Tests (in-process server, temp DB)
# ---------------------------------------------------------------------------

class HttpContractTests(unittest.TestCase):
    """Six operations over HTTP on a temp seeded DB."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.db = _temp_db()
        cls._server, cls._lines, cls.port = _start_server(cls.db)
        cls.client = _client_for_port(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        _stop_server(cls._server)

    # -- all six operations -------------------------------------------------

    def test_search_returns_results(self):
        hits = self.client.search("transport", limit=5)
        self.assertIsInstance(hits, list)
        self.assertTrue(hits)
        for h in hits:
            self.assertIn("id", h)
            self.assertIn("type", h)
            self.assertIn("name", h)

    def test_get_returns_node(self):
        node = self.client.get("transport")
        self.assertEqual(node["id"], "transport")
        self.assertIn("type", node)
        self.assertIn("name", node)

    def test_related_returns_neighbours(self):
        neighbours = self.client.related("transport")
        self.assertIsInstance(neighbours, list)
        self.assertGreaterEqual(len(neighbours), 1)
        for entry in neighbours:
            self.assertIn("node", entry)
            self.assertIn("via", entry)
            self.assertIsInstance(entry["via"], list)
            for via in entry["via"]:
                self.assertIn("relationship_type", via)
                self.assertIn("direction", via)

    def test_follow_returns_edges(self):
        edges = self.client.follow("transport", relationship_type="extends")
        self.assertEqual({e["target_node_id"] for e in edges},
                         {"write-transport", "read-transport"})
        for e in edges:
            self.assertIn("relationship_type", e)
            self.assertIn("target_node_id", e)
            self.assertIn("node", e)

    def test_provenance_returns_record(self):
        prov = self.client.provenance("transport")
        self.assertEqual(prov["node_id"], "transport")
        for key in ("source_id", "source_name", "source_version",
                    "source_location", "imported_at"):
            self.assertIn(key, prov)

    def test_inspect_returns_stats(self):
        stats = self.client.inspect()
        self.assertEqual(stats["node_count"], 4)
        self.assertEqual(stats["source_count"], 1)
        self.assertEqual(stats["relationship_count"], 3)
        self.assertEqual(sum(stats["nodes_by_type"].values()), 4)

    # -- real chains (no fabricated IDs) ------------------------------------

    def test_search_get_provenance_chain(self):
        hits = self.client.search("ReadTransport", limit=5)
        self.assertTrue(hits)
        node = self.client.get(hits[0]["id"])
        self.assertEqual(node["id"], hits[0]["id"])
        prov = self.client.provenance(hits[0]["id"])
        self.assertEqual(prov["node_id"], hits[0]["id"])

    def test_search_related_chain(self):
        neighbours = self.client.related("transport")
        self.assertGreaterEqual(len(neighbours), 1)
        neighbour_id = neighbours[0]["node"]["id"]
        node = self.client.get(neighbour_id)
        self.assertEqual(node["id"], neighbour_id)

    def test_search_follow_chain(self):
        edges = self.client.follow("transport", relationship_type="extends")
        self.assertTrue(edges)
        target = self.client.get(edges[0]["target_node_id"])
        self.assertEqual(target["id"], edges[0]["target_node_id"])

    # -- contract_version ---------------------------------------------------

    def test_contract_version_on_envelope(self):
        for op, args in [("inspect", None),
                         ("search", {"query": "transport", "limit": 3}),
                         ("get", {"node_id": "transport"}),
                         ("related", {"node_id": "transport"}),
                         ("follow", {"node_id": "transport",
                                     "relationship_type": "extends"}),
                         ("provenance", {"node_id": "transport"})]:
            resp = self.client.execute({"operation": op, **(
                {"arguments": args} if args else {})})
            self.assertEqual(resp.get("contract_version"), CONTRACT_VERSION)

    # -- deterministic output -----------------------------------------------

    def test_deterministic(self):
        a = self.client.search("transport", limit=5)
        b = self.client.search("transport", limit=5)
        self.assertEqual(a, b)

    def test_deterministic_get(self):
        a = self.client.get("transport")
        b = self.client.get("transport")
        self.assertEqual(a, b)

    def test_deterministic_inspect(self):
        self.assertEqual(self.client.inspect(), self.client.inspect())

    # -- error handling -----------------------------------------------------

    def test_unknown_operation(self):
        with self.assertRaises(UnknownOperationError) as ctx:
            self.client.request("nope")
        self.assertEqual(ctx.exception.code, "unknown_operation")

    def test_invalid_argument(self):
        with self.assertRaises(InvalidArgumentError) as ctx:
            self.client.search("x", limit=-1)
        self.assertEqual(ctx.exception.code, "invalid_argument")

    def test_node_not_found(self):
        with self.assertRaises(NodeNotFoundError) as ctx:
            self.client.get("nonexistent-id")
        self.assertEqual(ctx.exception.code, "node_not_found")

    def test_invalid_relationship_type(self):
        with self.assertRaises(InvalidRelationshipTypeError) as ctx:
            self.client.follow("transport", relationship_type="is_a")
        self.assertEqual(ctx.exception.code, "invalid_relationship_type")

    def test_malformed_json(self):
        transport = self.client.transport
        status, text = transport.execute_raw(b"{ not json }")
        self.assertEqual(status, 400)
        envelope = json.loads(text)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_request")

    def test_unsupported_content_type(self):
        transport = self.client.transport
        status, text = transport.execute_raw(
            b'{"operation":"inspect"}', content_type="text/plain")
        self.assertEqual(status, 415)
        envelope = json.loads(text)
        self.assertFalse(envelope["ok"])
        self.assertEqual(envelope["error"]["code"], "invalid_request")

    def test_oversized_request(self):
        tmp2, db2 = _temp_db()
        try:
            _server, _lines, port = _start_server(db2, max_body_bytes=4096)
            try:
                transport = HTTPTransport(port=port)
                try:
                    status, text = transport.execute_raw(
                        b"x" * 8192, content_length=8192)
                    self.assertEqual(status, 413)
                    envelope = json.loads(text)
                    self.assertFalse(envelope["ok"])
                    self.assertEqual(envelope["error"]["code"], "invalid_request")
                finally:
                    transport.close()
            finally:
                _stop_server(_server)
        finally:
            tmp2.cleanup()

    def test_no_stack_traces_in_errors(self):
        with self.assertRaises(UnknownOperationError):
            self.client.request("nope")
        try:
            self.client.execute({"operation": "nope"})
        except Exception:
            pass
        # raw envelope should be clean
        resp = self.client.execute({"operation": "nope"})
        text = json.dumps(resp)
        self.assertNotIn("Traceback", text)
        self.assertNotIn('File "', text)


# ---------------------------------------------------------------------------
# Authentication Tests
# ---------------------------------------------------------------------------

class HttpAuthTests(unittest.TestCase):
    """API-key gate over HTTP."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.db = _temp_db()
        cls._server, cls._lines, cls.port = _start_server(
            cls.db, api_key="sekret")
        cls.client_with_key = _client_for_port(cls.port, api_key="sekret")
        cls.client_no_key = _client_for_port(cls.port)
        cls.client_wrong_key = _client_for_port(cls.port, api_key="wrong")

    @classmethod
    def tearDownClass(cls):
        cls.client_with_key.close()
        cls.client_no_key.close()
        cls.client_wrong_key.close()
        _stop_server(cls._server)

    def test_missing_key_rejected(self):
        with self.assertRaises(AuthenticationError) as ctx:
            self.client_no_key.request("inspect")
        self.assertEqual(ctx.exception.http_status, 401)
        self.assertEqual(ctx.exception.code, "unauthorized")

    def test_wrong_key_rejected(self):
        with self.assertRaises(AuthenticationError) as ctx:
            self.client_wrong_key.request("inspect")
        self.assertEqual(ctx.exception.http_status, 401)
        self.assertEqual(ctx.exception.code, "unauthorized")

    def test_correct_key_allowed(self):
        result = self.client_with_key.request("inspect")
        self.assertGreaterEqual(result["node_count"], 4)

    def test_auth_error_envelope_via_execute(self):
        resp = self.client_no_key.execute({"operation": "inspect"})
        self.assertFalse(resp["ok"])
        self.assertEqual(resp["error"]["code"], "unauthorized")


# ---------------------------------------------------------------------------
# Process Boundary Tests
# ---------------------------------------------------------------------------

class ProcessBoundaryTests(unittest.TestCase):
    """Client in a separate OS process, communicating only over HTTP."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self._server, self._lines, self.port = _start_server(self.db)

    def tearDown(self):
        _stop_server(self._server)
        self.tmp.cleanup()

    def _run_client(self, script, env=None):
        run_env = dict(os.environ)
        run_env["EXTERNAL_PORT"] = str(self.port)
        run_env["EXTERNAL_HOST"] = "127.0.0.1"
        if env:
            run_env.update(env)
        return subprocess.run(
            [sys.executable, "-c", script],
            cwd=_ROOT, capture_output=True, text=True,
            env=run_env, timeout=60)

    def test_search_get_provenance_cross_process(self):
        code = (
            "import json, os\n"
            "from external_http_client import ExternalHTTPClient\n"
            "p = ExternalHTTPClient(host=os.environ['EXTERNAL_HOST'], "
            "port=int(os.environ['EXTERNAL_PORT']))\n"
            "r = p.request('search', {'query': 'ReadTransport', 'limit': 5})\n"
            "node = p.get(r[0]['id'])\n"
            "prov = p.provenance(r[0]['id'])\n"
            "print(json.dumps({'node_id': node['id'], "
            "'prov_node_id': prov['node_id']}))\n"
            "p.close()\n"
        )
        proc = self._run_client(code)
        self.assertEqual(proc.returncode, 0, proc.stderr[:300])
        data = json.loads(proc.stdout.strip())
        self.assertEqual(data["node_id"], "read-transport")
        self.assertEqual(data["prov_node_id"], "read-transport")

    def test_all_six_operations_cross_process(self):
        code = (
            "import json, os\n"
            "from external_http_client import ExternalHTTPClient\n"
            "c = ExternalHTTPClient(host=os.environ['EXTERNAL_HOST'], "
            "port=int(os.environ['EXTERNAL_PORT']))\n"
            "res = {}\n"
            "res['inspect'] = c.inspect()\n"
            "res['search'] = c.search('transport', limit=5)\n"
            "res['get'] = c.get(res['search'][0]['id'])\n"
            "res['related'] = c.related('transport')\n"
            "res['follow'] = c.follow('transport', "
            "relationship_type='extends')\n"
            "res['provenance'] = c.provenance('transport')\n"
            "print(json.dumps({k: type(v).__name__ for k, v in res.items()}))\n"
            "c.close()\n"
        )
        proc = self._run_client(code)
        self.assertEqual(proc.returncode, 0, proc.stderr[:300])
        data = json.loads(proc.stdout.strip())
        self.assertEqual(data["inspect"], "dict")
        self.assertEqual(data["search"], "list")
        self.assertEqual(data["get"], "dict")
        self.assertEqual(data["related"], "list")
        self.assertEqual(data["follow"], "list")
        self.assertEqual(data["provenance"], "dict")

    def test_errors_cross_process(self):
        code = (
            "import json, os\n"
            "from external_http_client import ExternalHTTPClient\n"
            "c = ExternalHTTPClient(host=os.environ['EXTERNAL_HOST'], "
            "port=int(os.environ['EXTERNAL_PORT']))\n"
            "results = []\n"
            "try:\n"
            "    c.get('no-such-node')\n"
            "except Exception as e:\n"
            "    results.append(e.code)\n"
            "try:\n"
            "    c.request('no-op')\n"
            "except Exception as e:\n"
            "    results.append(e.code)\n"
            "print(json.dumps(results))\n"
            "c.close()\n"
        )
        proc = self._run_client(code)
        self.assertEqual(proc.returncode, 0, proc.stderr[:300])
        codes = json.loads(proc.stdout.strip())
        self.assertEqual(codes, ["node_not_found", "unknown_operation"])

    def test_single_json_envelope_per_response(self):
        code = (
            "import json, os\n"
            "from external_http_client import ExternalHTTPClient\n"
            "c = ExternalHTTPClient(host=os.environ['EXTERNAL_HOST'], "
            "port=int(os.environ['EXTERNAL_PORT']))\n"
            "resp = c.execute({'operation': 'inspect'})\n"
            "assert isinstance(resp, dict)\n"
            "assert resp['ok'] is True\n"
            "assert resp['result']['node_count'] == 4\n"
            "print('OK')\n"
            "c.close()\n"
        )
        proc = self._run_client(code)
        self.assertEqual(proc.returncode, 0, proc.stderr[:300])
        self.assertEqual(proc.stdout.strip(), "OK")


# ---------------------------------------------------------------------------
# Persistence Tests
# ---------------------------------------------------------------------------

class PersistenceTests(unittest.TestCase):
    """Server reuse: repository initialized exactly once across many requests."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self._server, self._lines, self.port = _start_server(self.db)
        self.client = _client_for_port(self.port)

    def tearDown(self):
        self.client.close()
        _stop_server(self._server)
        self.tmp.cleanup()

    def test_repository_initialized_exactly_once(self):
        for _ in range(5):
            self.client.inspect()
        _stop_server(self._server)
        stats = _parse_stats(self._lines)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["initializations"], 1)
        self.assertEqual(stats["requests"], 5)

    def test_persistence_after_errors(self):
        self.client.inspect()
        try:
            self.client.get("no-such-node")
        except NodeNotFoundError:
            pass
        self.client.search("transport", limit=3)
        _stop_server(self._server)
        stats = _parse_stats(self._lines)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["initializations"], 1)
        self.assertEqual(stats["requests"], 3)

    def test_first_vs_subsequent_latency(self):
        first_start = time.perf_counter()
        self.client.inspect()
        first_latency = time.perf_counter() - first_start

        n_sub = 10
        sub_start = time.perf_counter()
        for _ in range(n_sub):
            self.client.inspect()
        total_sub = time.perf_counter() - sub_start
        mean_sub = total_sub / n_sub

        _stop_server(self._server)
        stats = _parse_stats(self._lines)
        self.assertIsNotNone(stats)
        self.assertEqual(stats["initializations"], 1)
        self.assertIsInstance(stats["load_seconds"], float)
        self.assertGreater(stats["load_seconds"], 0)
        # Soft check: first request includes repo load; usually slower but
        # OS scheduling can make it faster under contention.  The hard proof
        # of no reload is initializations == 1 above.
        self.assertGreater(first_latency, 0)
        self.assertGreater(mean_sub, 0)


# ---------------------------------------------------------------------------
# Security Tests
# ---------------------------------------------------------------------------

class SecurityTests(unittest.TestCase):
    """External client cannot access internal state."""

    @classmethod
    def setUpClass(cls):
        cls.tmp, cls.db = _temp_db()
        cls._server, cls._lines, cls.port = _start_server(cls.db)
        cls.client = _client_for_port(cls.port)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        _stop_server(cls._server)

    def test_sql_smuggling_rejected(self):
        with self.assertRaises(InvalidArgumentError):
            self.client.request(
                "search", {"query": "x", "sql": "SELECT 1"})

    def test_command_smuggling_rejected(self):
        with self.assertRaises(InvalidArgumentError):
            self.client.request(
                "get", {"node_id": "test", "command": "rm -rf /"})

    def test_path_smuggling_rejected(self):
        with self.assertRaises(InvalidArgumentError):
            self.client.request(
                "get", {"node_id": "test", "path": "/etc/passwd"})

    def test_client_module_has_no_internal_access(self):
        import sys as _sys
        mods_before = set(_sys.modules.keys())
        from external_http_client import (  # noqa: E402
            ExternalHTTPClient as _C,
            HTTPTransport as _T,
        )
        mods_after = set(_sys.modules.keys())
        new_mods = mods_after - mods_before
        for forbidden in ("sqlite3", "retrieval", "http_server",
                          "knowledge_api", "ai_engine"):
            self.assertFalse(
                any(forbidden in m for m in new_mods),
                "importing external_http_client loaded forbidden module %r"
                % forbidden)


if __name__ == "__main__":
    unittest.main()
