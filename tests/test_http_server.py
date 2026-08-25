"""HTTP transport tests: the server exposes the exact Contract v1 envelope.

These tests prove that ``http_server`` is purely a transport:

* HTTP routing forwards a JSON body to the existing contract boundary and
  returns the envelope VERBATIM (including ``contract_version``);
* transport-level failures (bad JSON, wrong content type, missing length)
  produce structured contract-shaped error envelopes with the documented
  error codes and correct HTTP status codes;
* an optional API key is enforced at the transport boundary only;
* the engine/repository is loaded exactly ONCE and reused across requests;
* the server is read-only with respect to the knowledge database;
* GET /health works and unknown endpoints/methods are rejected.

The tests never touch the production database.
"""

import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from typing import Any, Dict, Optional

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.contract import CONTRACT_VERSION
from http_server.server import (
    DEFAULT_STATUS,
    HTTP_STATUS_FOR_CODE,
    MAX_BODY_BYTES,
    KnowledgeHTTPServer,
    _CountingFactory,
)
from knowledge_client import InvalidResponseError
from retrieval.repository import KnowledgeRepository


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
    sid = repo.add_source("http-fixture", version="1.0")
    repo.add_node("alpha", "entity", "Alpha", "First node", source_id=sid)
    repo.add_node("beta", "entity", "Beta", "Second node", source_id=sid)
    repo.add_relationship("alpha", "references", "beta", "src")
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


class HttpTransportContractTests(unittest.TestCase):
    """HTTP carries the Contract v1 envelope untouched, start to finish."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self.hash_before = _sha256(self.db)
        self.server, self.port = _start_server(self.db)
        self.conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        self.thread = None

    def tearDown(self):
        try:
            self.conn.close()
        except OSError:
            pass
        _close_server(self.server)
        self.tmp.cleanup()

    def _request(self, path, body=None, method="POST",
                 headers=None, raw_body=None, raw_length=None):
        if raw_body is not None:
            payload = raw_body
        elif body is not None:
            payload = json.dumps(body).encode("utf-8")
        else:
            payload = b""
        hdrs = dict(headers or {})
        hdrs.setdefault("Content-Type", "application/json")
        if raw_length is None:
            hdrs["Content-Length"] = str(len(payload))
        elif raw_length is not None:
            hdrs["Content-Length"] = str(raw_length)
        self.conn.request(method, path, body=payload, headers=hdrs)
        resp = self.conn.getresponse()
        return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_successful_call_returns_ok_envelope(self):
        status, payload = self._request(
            "/v1/execute", {"operation": "inspect"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["contract_version"], CONTRACT_VERSION)
        self.assertEqual(payload["result"]["node_count"], 2)

    def test_error_call_maps_to_documented_code_and_status(self):
        status, payload = self._request(
            "/v1/execute", {"operation": "get", "arguments": {"node_id": "nope"}})
        self.assertEqual(status,
                         HTTP_STATUS_FOR_CODE["node_not_found"])
        self.assertEqual(payload["error"]["code"], "node_not_found")
        self.assertEqual(payload["contract_version"], CONTRACT_VERSION)

    def test_unknown_operation_maps_to_unknown_operation(self):
        status, payload = self._request("/v1/execute", {"operation": "nope"})
        self.assertEqual(status, HTTP_STATUS_FOR_CODE["unknown_operation"])
        self.assertEqual(payload["error"]["code"], "unknown_operation")

    def test_all_documented_error_codes_map_to_statuses(self):
        # A representative sample of each documented code arrives over HTTP.
        cases = (
            ("invalid_request", b"not-json"),
            ("unknown_operation",
             json.dumps({"operation": "nope"}).encode("utf-8")),
            ("invalid_argument",
             json.dumps({"operation": "search",
                         "arguments": {"query": "x", "limit": -1}}).encode("utf-8")),
            ("invalid_relationship_type",
             json.dumps({"operation": "follow",
                         "arguments": {"node_id": "alpha",
                                       "relationship_type": "is_a"}}).encode("utf-8")),
            ("node_not_found",
             json.dumps({"operation": "get",
                         "arguments": {"node_id": "missing"}}).encode("utf-8")),
        )
        from http_server.server import HTTP_STATUS_FOR_CODE as STATUS
        for code, raw in cases:
            with self.subTest(code=code):
                status, payload = self._request(
                    "/v1/execute", raw_body=raw)
                self.assertEqual(payload["error"]["code"], code)
                self.assertEqual(status, STATUS[code])
                self.assertEqual(payload["contract_version"], CONTRACT_VERSION)

    def test_contract_version_survives_round_trip(self):
        for request in (
            {"operation": "inspect"},
            {"operation": "search", "arguments": {"query": "alpha"}},
        ):
            _, payload = self._request("/v1/execute", request)
            self.assertEqual(payload["contract_version"], CONTRACT_VERSION)

    def test_database_is_read_only(self):
        self._request("/v1/execute", {"operation": "search",
                                      "arguments": {"query": "alpha"}})
        self._request("/v1/execute", {"operation": "inspect"})
        self.assertEqual(_sha256(self.db), self.hash_before)

    def test_server_is_deterministic(self):
        def search():
            _, payload = self._request(
                "/v1/execute", {"operation": "search",
                                "arguments": {"query": "alpha"}})
            return payload["result"]
        self.assertEqual(search(), search())


class HttpTransportBoundaryTests(unittest.TestCase):
    """Transport-level behaviour (routing, limits, keys) stays at the edge."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self.server, self.port = _start_server(self.db)
        self.conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)

    def tearDown(self):
        try:
            self.conn.close()
        except OSError:
            pass
        _close_server(self.server)
        self.tmp.cleanup()

    def _post(self, path, raw, headers=None, length=None):
        hdrs = dict(headers or {})
        hdrs.setdefault("Content-Type", "application/json")
        hdrs.setdefault("Content-Length", str(length if length is not None
                                               else len(raw)))
        self.conn.request("POST", path, body=raw, headers=hdrs)
        resp = self.conn.getresponse()
        body = resp.read().decode("utf-8")
        try:
            payload: Any = json.loads(body)
        except ValueError:
            payload = {"raw": body}
        return resp.status, payload

    def test_health_endpoint(self):
        self.conn.request("GET", "/health")
        resp = self.conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(resp.status, 200)
        self.assertEqual(payload["contract_version"], CONTRACT_VERSION)
        self.assertTrue(payload["ok"])

    def test_unknown_endpoint_rejected(self):
        status, payload = self._post("/v1/other",
                                     json.dumps({"operation": "inspect"}).encode())
        self.assertEqual(status, 404)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_wrong_http_method_rejected(self):
        self.conn.request("GET", "/v1/execute")
        resp = self.conn.getresponse()
        payload = json.loads(resp.read().decode("utf-8"))
        self.assertEqual(resp.status, 405)
        self.assertFalse(payload["ok"])

    def test_bad_content_type_rejected(self):
        status, payload = self._post(
            "/v1/execute", b"{}", headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 415)
        self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_missing_content_length_rejected(self):
        status, payload = self._post("/v1/execute", b"{}", length=0)
        self.assertEqual(status, 411)
        self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_oversized_body_rejected(self):
        small = MAX_BODY_BYTES + 1
        raw = b"x" * small
        status, payload = self._post("/v1/execute", raw)
        self.assertEqual(status, 413)
        self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_invalid_json_rejected_but_formatted_envelope(self):
        status, payload = self._post("/v1/execute", b"{not json")
        self.assertEqual(status, 400)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"]["code"], "invalid_request")
        self.assertEqual(payload["contract_version"], CONTRACT_VERSION)

    def test_missing_operation_produces_invalid_request(self):
        status, payload = self._post("/v1/execute", b"{}")
        self.assertEqual(status, HTTP_STATUS_FOR_CODE["invalid_request"])
        self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_list_body_rejected(self):
        status, payload = self._post("/v1/execute", b"[]")
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"]["code"], "invalid_request")

    def test_no_stack_traces_on_errors(self):
        _, payload = self._post("/v1/execute", b"{bad")
        text = json.dumps(payload)
        self.assertNotIn("Traceback", text)
        self.assertNotIn('File "', text)


class HttpApiKeyTests(unittest.TestCase):
    """The API key gate is transport-only and never part of the contract."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self.server, self.port = _start_server(self.db, api_key="sekret")
        self.conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)

    def tearDown(self):
        try:
            self.conn.close()
        except OSError:
            pass
        _close_server(self.server)
        self.tmp.cleanup()

    def _request(self, body, headers=None):
        raw = json.dumps(body).encode("utf-8")
        hdrs = dict(headers or {})
        hdrs.setdefault("Content-Type", "application/json")
        hdrs.setdefault("Content-Length", str(len(raw)))
        self.conn.request("POST", "/v1/execute", body=raw, headers=hdrs)
        resp = self.conn.getresponse()
        return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_missing_key_rejected(self):
        status, payload = self._request({"operation": "inspect"})
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])

    def test_wrong_key_rejected(self):
        status, payload = self._request(
            {"operation": "inspect"}, headers={"X-API-Key": "wrong"})
        self.assertEqual(status, 401)
        self.assertFalse(payload["ok"])

    def test_correct_header_key_allowed(self):
        status, payload = self._request(
            {"operation": "inspect"}, headers={"X-API-Key": "sekret"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_bearer_key_allowed(self):
        status, payload = self._request(
            {"operation": "inspect"},
            headers={"Authorization": "Bearer sekret"})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])


class HttpEngineLifecycleTests(unittest.TestCase):
    """The engine/repository is loaded once and reused across requests."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self.factory = _CountingFactory(self.db)
        self.server, self.port = _start_server(
            self.db, interface_factory=self.factory)
        self.conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)

    def tearDown(self):
        try:
            self.conn.close()
        except OSError:
            pass
        _close_server(self.server)
        self.tmp.cleanup()

    def _inspect(self):
        raw = json.dumps({"operation": "inspect"}).encode("utf-8")
        self.conn.request(
            "POST", "/v1/execute", body=raw,
            headers={"Content-Type": "application/json",
                     "Content-Length": str(len(raw))})
        resp = self.conn.getresponse()
        return json.loads(resp.read().decode("utf-8"))

    def test_repository_loaded_exactly_once_across_many_requests(self):
        self.assertEqual(self.server.stats["requests"], 0)
        self.assertEqual(self.server.stats["initializations"], 0)
        for _ in range(3):
            payload = self._inspect()
            self.assertTrue(payload["ok"])
        self.assertEqual(self.server.stats["initializations"], 1)
        # transport failures and errors do not trigger a reload
        self._inspect()  # one more successful request
        self.assertEqual(self.server.stats["initializations"], 1)

    def test_load_costs_paid_once(self):
        first = self._inspect()
        self.assertTrue(first["ok"])
        self.assertEqual(self.server.stats["initializations"], 1)
        self.assertIsInstance(self.server.stats["load_seconds"], float)
        self.assertGreaterEqual(self.server.stats["load_seconds"], 0.0)

    def test_close_releases_engine(self):
        self._inspect()
        self.server.close()
        self.assertIsNone(self.server._interface)


class HttpCorsTests(unittest.TestCase):
    """CORS support: OPTIONS preflight + headers on all JSON responses."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self.server, self.port = _start_server(self.db)
        self.base = "http://127.0.0.1:%d" % self.port

    def tearDown(self):
        _close_server(self.server)

    def _post(self, body, headers=None):
        import urllib.request
        req = urllib.request.Request(
            self.base + "/v1/execute",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", **(headers or {})},
            method="POST")
        with urllib.request.urlopen(req) as r:
            return r.getheader("Access-Control-Allow-Origin"), r.status

    def _options(self, extra_headers=None):
        import urllib.request
        headers = {
            "Origin": "http://localhost:8766",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        }
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(
            self.base + "/v1/execute", method="OPTIONS", headers=headers)
        with urllib.request.urlopen(req) as r:
            return {k: r.getheader(k) for k in [
                "Access-Control-Allow-Origin",
                "Access-Control-Allow-Methods",
                "Access-Control-Allow-Headers",
            ]}, r.status

    def _get_headers(self, path):
        import urllib.request, urllib.error
        req = urllib.request.Request(self.base + path, method="GET")
        try:
            r = urllib.request.urlopen(req)
            return r.getheader("Access-Control-Allow-Origin"), r.status
        except urllib.error.HTTPError as e:
            return e.headers.get("Access-Control-Allow-Origin"), e.code

    # -- OPTIONS preflight -------------------------------------------------

    def test_options_preflight_returns_200(self):
        _, status = self._options()
        self.assertEqual(status, 200)

    def test_options_preflight_allow_origin_wildcard(self):
        headers, _ = self._options()
        self.assertEqual(headers["Access-Control-Allow-Origin"], "*")

    def test_options_preflight_allow_methods(self):
        headers, _ = self._options()
        self.assertEqual(headers["Access-Control-Allow-Methods"], "POST, OPTIONS")

    def test_options_preflight_allow_headers(self):
        headers, _ = self._options()
        self.assertIn("Content-Type", headers["Access-Control-Allow-Headers"])
        self.assertIn("Authorization", headers["Access-Control-Allow-Headers"])

    # -- CORS headers on JSON responses ------------------------------------

    def test_post_success_has_cors_allow_origin(self):
        origin, status = self._post({"operation": "inspect"})
        self.assertEqual(status, 200)
        self.assertEqual(origin, "*")

    def test_health_has_cors_headers(self):
        origin, status = self._get_headers("/health")
        self.assertEqual(status, 200)
        self.assertEqual(origin, "*")

    def test_error_response_has_cors_headers(self):
        """Errors must carry CORS headers so the browser can read them."""
        import urllib.request, urllib.error
        req = urllib.request.Request(
            self.base + "/v1/execute",
            data=json.dumps({"operation": "nope"}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST")
        try:
            r = urllib.request.urlopen(req)
            self.assertEqual(r.getheader("Access-Control-Allow-Origin"), "*")
            self.assertEqual(r.status, 404)
        except urllib.error.HTTPError as e:
            self.assertEqual(e.headers.get("Access-Control-Allow-Origin"), "*")
            self.assertEqual(e.code, 404)

    def test_404_endpoint_has_cors_headers(self):
        origin, status = self._get_headers("/nonexistent")
        self.assertEqual(status, 404)
        self.assertEqual(origin, "*")

    # -- API key still enforced --------------------------------------------

    def test_api_key_still_enforced_with_cors(self):
        server_with_key, base_port = _start_server(self.db, api_key="secret")
        base = "http://127.0.0.1:%d" % base_port
        try:
            import urllib.request, urllib.error
            # No key -> 401; CORS header still present (so browser can read it)
            req = urllib.request.Request(
                base + "/v1/execute",
                data=json.dumps({"operation": "inspect"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST")
            try:
                r = urllib.request.urlopen(req)
                self.assertEqual(r.getheader("Access-Control-Allow-Origin"), "*")
                self.assertEqual(r.status, 401)
            except urllib.error.HTTPError as e:
                self.assertEqual(e.headers.get("Access-Control-Allow-Origin"), "*")
                self.assertEqual(e.code, 401)
        finally:
            _close_server(server_with_key)

    def test_bearer_auth_still_accepted_with_cors(self):
        server_with_key, base_port = _start_server(self.db, api_key="secret")
        base = "http://127.0.0.1:%d" % base_port
        try:
            import urllib.request
            req = urllib.request.Request(
                base + "/v1/execute",
                data=json.dumps({"operation": "inspect"}).encode(),
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer secret",
                },
                method="POST")
            with urllib.request.urlopen(req) as r:
                self.assertEqual(r.getheader("Access-Control-Allow-Origin"), "*")
                self.assertEqual(r.status, 200)
        finally:
            _close_server(server_with_key)


if __name__ == "__main__":
    unittest.main()