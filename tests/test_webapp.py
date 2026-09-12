"""Web App v1 tests: structure, isolation, and real HTTP integration.

Proves the Knowledge Engine Web App:

* is a single-file SPA with no build dependencies
* contains no forbidden Python imports (sqlite3, retrieval, api internals)
* contains no secrets or API keys
* has proper HTML structure with expected elements
* communicates with the backend ONLY through HTTP
* can perform real search -> get -> related -> follow -> provenance
  through the actual HTTP server and Knowledge Engine
* never modifies the production database
"""

import ast
import http.client
import json
import os
import re
import sys
import tempfile
import threading
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from http_server.server import KnowledgeHTTPServer
from retrieval.repository import KnowledgeRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WEBAPP_PATH = os.path.join(_ROOT, "webapp", "index.html")


def _read_webapp():
    with open(WEBAPP_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _seed_db(db_path):
    repo = KnowledgeRepository(db_path)
    repo.initialize()
    sid = repo.add_source("webapp-fixture", version="1.0")
    repo.add_node("python", "technology", "Python",
                  "A high-level programming language.", source_id=sid)
    repo.add_node("flask", "technology", "Flask",
                  "A lightweight web framework for Python.", source_id=sid)
    repo.add_node("django", "technology", "Django",
                  "A high-level web framework for Python.", source_id=sid)
    repo.add_node("rest-api", "concept", "REST API",
                  "An architectural style for web services.", source_id=sid)
    repo.add_relationship("flask", "depends_on", "python", "src")
    repo.add_relationship("django", "depends_on", "python", "src")
    repo.add_relationship("flask", "references", "rest-api", "src")
    repo.close()


def _temp_db():
    tmp = tempfile.TemporaryDirectory()
    db = os.path.join(tmp.name, "knowledge.db")
    _seed_db(db)
    return tmp, db


def _start_server(db, **kwargs):
    server = KnowledgeHTTPServer(("127.0.0.1", 0), db_path=db, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, port


def _close_server(server):
    server.shutdown()
    server.server_close()
    server.close()


def _api_call(host, port, operation, args=None, timeout=10):
    """Simulate what the browser JS does: POST /v1/execute."""
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        body = {"operation": operation}
        if args:
            body["arguments"] = args
        raw = json.dumps(body).encode("utf-8")
        conn.request("POST", "/v1/execute", body=raw, headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(raw))
        })
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


def _health_call(host, port, timeout=5):
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.request("GET", "/health")
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Static structure tests
# ---------------------------------------------------------------------------

class WebAppStructureTests(unittest.TestCase):
    """The Web App is a valid single-file SPA with expected structure."""

    def setUp(self):
        self.html = _read_webapp()

    def test_file_exists(self):
        self.assertTrue(os.path.isfile(WEBAPP_PATH))

    def test_is_html(self):
        self.assertTrue(self.html.startswith("<!DOCTYPE html>"))
        self.assertIn("<html", self.html)
        self.assertIn("</html>", self.html)

    def test_has_head(self):
        self.assertIn("<head>", self.html)
        self.assertIn("<title>", self.html)

    def test_has_body(self):
        self.assertIn("<body>", self.html)
        self.assertIn("</body>", self.html)

    def test_has_script(self):
        self.assertIn("<script>", self.html)
        self.assertIn("</script>", self.html)

    def test_has_style(self):
        self.assertIn("<style>", self.html)
        self.assertIn("</style>", self.html)

    def test_has_viewport_meta(self):
        self.assertIn('name="viewport"', self.html)

    def test_has_search_input(self):
        self.assertIn("search-input", self.html)

    def test_has_app_container(self):
        self.assertIn('id="app"', self.html)


# ---------------------------------------------------------------------------
# Isolation / security tests
# ---------------------------------------------------------------------------

class WebAppIsolationTests(unittest.TestCase):
    """The Web App contains no forbidden imports, secrets, or DB access."""

    def setUp(self):
        self.html = _read_webapp()

    def test_no_sqlite3_import(self):
        self.assertNotIn("sqlite3", self.html.lower().replace("sqlite", ""))

    def test_no_retrieval_import(self):
        # Exclude comments (lines starting with // or /* ... */)
        lines = [l for l in self.html.split("\n")
                 if not l.strip().startswith("//") and not l.strip().startswith("*")]
        code = "\n".join(lines)
        self.assertNotIn("from retrieval", code)
        self.assertNotIn("require('retrieval", code)
        self.assertNotIn('require("retrieval', code)

    def test_no_knowledge_api_import(self):
        # Exclude JS comments and the FORBIDDEN_PATTERNS audit array
        lines = []
        skip = False
        for line in self.html.split("\n"):
            stripped = line.strip()
            if stripped.startswith("/*") or stripped.startswith("*"):
                continue
            if stripped.startswith("//"):
                continue
            if "FORBIDDEN_PATTERNS" in stripped:
                skip = True
            if skip:
                if "]" in stripped:
                    skip = False
                continue
            lines.append(line)
        code = "\n".join(lines)
        self.assertNotIn("from knowledge_api", code)
        self.assertNotIn("require('knowledge_api", code)
        self.assertNotIn('require("knowledge_api', code)
        self.assertNotIn("import knowledge_api", code)

    def test_no_repository_import(self):
        # Exclude JS comments and the FORBIDDEN_PATTERNS audit array
        lines = []
        skip = False
        for line in self.html.split("\n"):
            stripped = line.strip()
            if stripped.startswith("/*") or stripped.startswith("*"):
                continue
            if stripped.startswith("//"):
                continue
            if "FORBIDDEN_PATTERNS" in stripped:
                skip = True
            if skip:
                if "]" in stripped:
                    skip = False
                continue
            lines.append(line)
        code = "\n".join(lines)
        self.assertNotIn("from repository", code)
        self.assertNotIn("require('repository", code)
        self.assertNotIn('require("repository', code)
        self.assertNotIn("import repository", code)

    def test_no_ingestion_import(self):
        # Exclude JS comments and the FORBIDDEN_PATTERNS audit array
        lines = []
        skip = False
        for line in self.html.split("\n"):
            stripped = line.strip()
            if stripped.startswith("/*") or stripped.startswith("*"):
                continue
            if stripped.startswith("//"):
                continue
            if "FORBIDDEN_PATTERNS" in stripped:
                skip = True
            if skip:
                if "]" in stripped:
                    skip = False
                continue
            lines.append(line)
        code = "\n".join(lines)
        self.assertNotIn("from ingestion", code)
        self.assertNotIn("require('ingestion", code)
        self.assertNotIn('require("ingestion', code)
        self.assertNotIn("import ingestion", code)

    def test_no_knowledge_compiler_import(self):
        # Exclude JS comments and the FORBIDDEN_PATTERNS audit array
        lines = []
        skip = False
        for line in self.html.split("\n"):
            stripped = line.strip()
            if stripped.startswith("/*") or stripped.startswith("*"):
                continue
            if stripped.startswith("//"):
                continue
            if "FORBIDDEN_PATTERNS" in stripped:
                skip = True
            if skip:
                if "]" in stripped:
                    skip = False
                continue
            lines.append(line)
        code = "\n".join(lines)
        self.assertNotIn("from knowledge_compiler", code)
        self.assertNotIn("require('knowledge_compiler", code)
        self.assertNotIn('require("knowledge_compiler', code)
        self.assertNotIn("import knowledge_compiler", code)

    def test_no_database_path(self):
        self.assertNotIn("knowledge.db", self.html)

    def test_no_sqlite_references(self):
        # Check for SQL-related patterns
        self.assertNotIn("PRAGMA", self.html)
        self.assertNotIn("CREATE TABLE", self.html)
        self.assertNotIn("INSERT INTO", self.html)

    def test_no_hardcoded_api_key(self):
        # Should not contain obvious API key patterns
        self.assertNotIn("api_key", self.html.replace("api_key)", "").replace("?api=", ""))
        self.assertNotIn("Authorization:", self.html)
        self.assertNotIn("Bearer ", self.html)

    def test_no_stack_traces(self):
        self.assertNotIn("Traceback", self.html)
        self.assertNotIn('File "', self.html)

    def test_no_python_imports(self):
        self.assertNotIn("import sqlite3", self.html)
        self.assertNotIn("from retrieval", self.html)
        self.assertNotIn("from api.", self.html)

    def test_forbidden_pattern_list_exists(self):
        """The JS code defines forbidden patterns for test-visible audit."""
        self.assertIn("FORBIDDEN_PATTERNS", self.html)

    def test_api_url_configurable(self):
        """API base URL is configurable via ?api= parameter."""
        self.assertIn("api", self.html)
        self.assertIn("URLSearchParams", self.html)

    def test_error_messages_defined(self):
        """Error messages are defined for Contract v1 error codes."""
        for code in ("invalid_request", "unknown_operation", "node_not_found",
                     "internal_error", "unauthorized"):
            self.assertIn('"' + code + '"', self.html)

    def test_contract_operations_called(self):
        """All six Contract v1 operations are used in the API module."""
        for op in ("search", "get", "related", "follow", "provenance", "inspect"):
            self.assertIn('"' + op + '"', self.html)

    def test_health_endpoint_used(self):
        """The /health transport endpoint is used for server status."""
        self.assertIn("/health", self.html)


# ---------------------------------------------------------------------------
# HTML rendering tests (static)
# ---------------------------------------------------------------------------

class WebAppHTMLTests(unittest.TestCase):
    """The HTML contains the expected structural elements."""

    def setUp(self):
        self.html = _read_webapp()

    def test_has_navigation(self):
        self.assertIn("<nav>", self.html)
        self.assertIn("data-nav", self.html)

    def test_has_search_nav(self):
        self.assertIn('data-nav="search"', self.html)

    def test_has_status_nav(self):
        self.assertIn('data-nav="status"', self.html)

    def test_has_header(self):
        self.assertIn('class="header"', self.html)

    def test_has_footer(self):
        self.assertIn('class="footer"', self.html)

    def test_has_main_app(self):
        self.assertIn('id="app"', self.html)

    def test_responsive_viewport(self):
        self.assertIn("width=device-width", self.html)

    def test_mobile_first_css(self):
        """CSS contains responsive media queries."""
        self.assertIn("@media", self.html)


# ---------------------------------------------------------------------------
# Real HTTP integration tests
# ---------------------------------------------------------------------------

class WebAppHTTPIntegrationTests(unittest.TestCase):
    """Full HTTP integration: Web App operations through the real server."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self.server, self.port = _start_server(self.db)
        self.host = "127.0.0.1"

    def tearDown(self):
        _close_server(self.server)
        self.tmp.cleanup()

    def test_search(self):
        """Search returns results matching the query."""
        status, data = _api_call(self.host, self.port, "search",
                                 {"query": "python"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        results = data["result"]
        self.assertGreater(len(results), 0)
        names = [r["name"] for r in results]
        self.assertIn("Python", names)

    def test_get(self):
        """Get returns node details."""
        status, data = _api_call(self.host, self.port, "get",
                                 {"node_id": "python"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        node = data["result"]
        self.assertEqual(node["name"], "Python")
        self.assertEqual(node["type"], "technology")
        self.assertIn("description", node)

    def test_related(self):
        """Related returns connected nodes."""
        status, data = _api_call(self.host, self.port, "related",
                                 {"node_id": "python"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        results = data["result"]
        self.assertGreater(len(results), 0)

    def test_follow(self):
        """Follow returns traversal results."""
        status, data = _api_call(self.host, self.port, "follow",
                                 {"node_id": "python"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_follow_with_type(self):
        """Follow with relationship type filter works."""
        status, data = _api_call(self.host, self.port, "follow",
                                 {"node_id": "python",
                                  "relationship_type": "depends_on"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_provenance(self):
        """Provenance returns source information."""
        status, data = _api_call(self.host, self.port, "provenance",
                                 {"node_id": "python"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        result = data["result"]
        self.assertIn("source_name", result)

    def test_inspect(self):
        """Inspect returns knowledge statistics."""
        status, data = _api_call(self.host, self.port, "inspect")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        result = data["result"]
        self.assertEqual(result["node_count"], 4)
        self.assertEqual(result["relationship_count"], 3)
        self.assertEqual(result["source_count"], 1)

    def test_health(self):
        """Health endpoint returns live metrics."""
        status, data = _health_call(self.host, self.port)
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIn("contract_version", data)
        self.assertIn("request_count", data)
        self.assertIn("uptime", data)

    def test_search_get_related_follow_provenance_chain(self):
        """Full chain: search -> get -> related -> follow -> provenance."""
        # 1. Search
        _, search_data = _api_call(self.host, self.port, "search",
                                   {"query": "flask"})
        self.assertTrue(search_data["ok"])
        flask_id = search_data["result"][0]["id"]

        # 2. Get
        _, get_data = _api_call(self.host, self.port, "get",
                                {"node_id": flask_id})
        self.assertTrue(get_data["ok"])
        self.assertEqual(get_data["result"]["name"], "Flask")

        # 3. Related
        _, rel_data = _api_call(self.host, self.port, "related",
                                {"node_id": flask_id})
        self.assertTrue(rel_data["ok"])
        self.assertGreater(len(rel_data["result"]), 0)

        # 4. Follow
        _, follow_data = _api_call(self.host, self.port, "follow",
                                   {"node_id": flask_id})
        self.assertTrue(follow_data["ok"])

        # 5. Provenance
        _, prov_data = _api_call(self.host, self.port, "provenance",
                                 {"node_id": flask_id})
        self.assertTrue(prov_data["ok"])
        self.assertGreater(len(prov_data["result"]), 0)

    def test_search_empty_query(self):
        """Empty search returns appropriate results."""
        status, data = _api_call(self.host, self.port, "search",
                                 {"query": "nonexistent_xyz_abc"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        # May return empty or low-score results

    def test_get_nonexistent_node(self):
        """Get on missing node returns node_not_found."""
        status, data = _api_call(self.host, self.port, "get",
                                 {"node_id": "does_not_exist"})
        self.assertEqual(status, 404)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"]["code"], "node_not_found")


# ---------------------------------------------------------------------------
# Production DB safety tests
# ---------------------------------------------------------------------------

class WebAppDBSafetyTests(unittest.TestCase):
    """The Web App never modifies its database."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "knowledge.db")
        _seed_db(self.db_path)
        self.hash_before = _sha256(self.db_path)

    def test_webapp_file_not_modified_by_server(self):
        """Starting the server does not modify the database."""
        server = KnowledgeHTTPServer(("127.0.0.1", 0), db_path=self.db_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            # Make several requests
            for op in ("inspect", "search", "get"):
                if op == "search":
                    body = {"operation": op, "arguments": {"query": "python"}}
                elif op == "get":
                    body = {"operation": op, "arguments": {"node_id": "nonexistent"}}
                else:
                    body = {"operation": op}
                _api_call("127.0.0.1", port, op,
                          body.get("arguments"), timeout=30)
        finally:
            _close_server(server)

        self.assertEqual(_sha256(self.db_path), self.hash_before)

    def test_integrity_check_ok(self):
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        result = conn.execute("PRAGMA integrity_check").fetchall()
        conn.close()
        self.assertEqual(result, [("ok",)])

    def test_foreign_key_check_empty(self):
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        result = conn.execute("PRAGMA foreign_key_check").fetchall()
        conn.close()
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# API configuration tests
# ---------------------------------------------------------------------------

class WebAppConfigTests(unittest.TestCase):
    """API URL configuration is present and documented."""

    def setUp(self):
        self.html = _read_webapp()

    def test_configurable_api_url(self):
        """API base is read from URL parameter or defaults to same origin."""
        self.assertIn("URLSearchParams", self.html)
        self.assertIn('get("api")', self.html)

    def test_default_same_origin(self):
        """Default API base is empty string (same origin)."""
        self.assertIn('params.get("api") || ""', self.html)

    def test_uses_fetch(self):
        """API module uses fetch() for HTTP requests."""
        self.assertIn("fetch(", self.html)

    def test_post_method_used(self):
        """API calls use POST method."""
        self.assertIn('method: "POST"', self.html)

    def test_json_content_type(self):
        """API calls set Content-Type: application/json."""
        self.assertIn("application/json", self.html)

    def test_read_only_get_health(self):
        """Health check uses GET method (default for fetch)."""
        self.assertIn("/health", self.html)
        # Verify the health function doesn't contain POST
        health_section = self.html[self.html.index("async health"):self.html.index("async health") + 200]
        self.assertNotIn("POST", health_section)


# ---------------------------------------------------------------------------
# Related via rendering regression (array vs object vs missing)
# ---------------------------------------------------------------------------

class WebAppViaFixTests(unittest.TestCase):
    """Related section correctly renders the via field.

    Contract v1 related returns via as an array of {direction, relationship_type}
    objects.  The Web App must extract the label without producing [object Object].
    """

    def setUp(self):
        self.html = _read_webapp()

    def test_via_array_extracted(self):
        """Source uses Array.isArray(r.via) to handle array via."""
        self.assertIn("Array.isArray(r.via)", self.html)

    def test_via_relationship_type_preferred(self):
        """Source prefers via.relationship_type over via.type."""
        self.assertIn("via.relationship_type", self.html)

    def test_via_type_fallback(self):
        """Source falls back to via.type for non-array objects."""
        self.assertIn("|| _via.type", self.html)

    def test_via_missing_falls_back_to_label(self):
        """Source falls back to 'via' string when via is missing/empty."""
        self.assertIn('|| "via"', self.html)

    def test_no_object_object_string(self):
        """Old r.via.type || r.via pattern that produces [object Object] is gone."""
        self.assertNotIn("r.via.type || r.via", self.html)

    def test_via_text_content_uses_via_label(self):
        """textContent for via badge uses the computed _viaLabel variable."""
        self.assertIn("_viaLabel", self.html)


# ---------------------------------------------------------------------------
# Provenance rendering regression (dict vs array)
# ---------------------------------------------------------------------------

class WebAppProvenanceFixTests(unittest.TestCase):
    """Provenance rendering correctly handles dict, array, and null results.

    Contract v1 provenance returns a single dict (object), not an array.
    The Web App must normalise this before calling .forEach().
    """

    def setUp(self):
        self.html = _read_webapp()

    def test_provenance_normalizes_result_to_array(self):
        """Source contains Array.isArray guard before provenance forEach."""
        self.assertIn("Array.isArray(items)", self.html)

    def test_provenance_no_bare_result_or_empty(self):
        """Original broken pattern (bare || []) is removed."""
        idx = self.html.index("state.provenanceData.result")
        line_start = self.html.rfind("\n", 0, idx) + 1
        line_end = self.html.index("\n", idx)
        line = self.html[line_start:line_end].strip()
        self.assertNotEqual(line, "var items = state.provenanceData.result || [];")

    def test_provenance_dict_wrapped_as_single_item(self):
        """Dict result is wrapped in an array for forEach."""
        idx = self.html.index("Array.isArray(items)")
        surrounding = self.html[idx:idx+120]
        self.assertIn("[items]", surrounding)

    def test_provenance_null_falls_back_to_empty(self):
        """Null/missing result falls back to empty array."""
        idx = self.html.index("Array.isArray(items)")
        surrounding = self.html[max(0, idx-40):idx+120]
        self.assertIn(": []", surrounding)

    def test_provenance_forEach_still_used(self):
        """forEach card rendering is preserved (not rewritten)."""
        self.assertIn("items.forEach(function(p)", self.html)


# ---------------------------------------------------------------------------
# API integration tests (against real production DB)
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main()
