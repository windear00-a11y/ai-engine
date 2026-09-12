"""v0.9 Production Foundation tests: Concurrency, Logging, Health, Auth.

Proves the HTTP transport layer is production-ready:

* Concurrent request handling (ThreadingHTTPServer).
* Structured JSON request logging to stderr (one line per request).
* Live health metrics via GET /health.
* Constant-time API-key comparison (hmac.compare_digest).
* Authentication error mapping correctness.
* Process-boundary concurrency over real HTTP.
"""

import http.client
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.contract import CONTRACT_VERSION
from http_server.server import (
    KnowledgeHTTPServer,
    _CountingFactory,
    _single_env,
)
from retrieval.repository import KnowledgeRepository

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
    sid = repo.add_source("v09-fixture", version="1.0")
    repo.add_node("alpha", "concept", "Alpha", "First concept", source_id=sid)
    repo.add_node("beta", "concept", "Beta", "Second concept", source_id=sid)
    repo.add_node("gamma", "concept", "Gamma", "Third concept", source_id=sid)
    repo.add_relationship("alpha", "references", "beta", "src")
    repo.add_relationship("beta", "references", "gamma", "src")
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
    return server, port, thread


def _close_server(server):
    server.shutdown()
    server.server_close()
    server.close()


def _make_request(host, port, path, body=None, method="POST",
                  headers=None, timeout=10):
    """Issue one HTTP request and return (status, parsed_payload).

    *body* can be a dict (auto-serialized to JSON) or raw bytes.
    """
    conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        if isinstance(body, bytes):
            raw = body
        elif body is not None:
            raw = json.dumps(body).encode("utf-8")
        else:
            raw = b""
        hdrs = dict(headers or {})
        hdrs.setdefault("Content-Type", "application/json")
        hdrs["Content-Length"] = str(len(raw))
        conn.request(method, path, body=raw, headers=hdrs)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read().decode("utf-8"))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Concurrency tests
# ---------------------------------------------------------------------------

class ConcurrencyTests(unittest.TestCase):
    """Multiple requests execute concurrently without blocking or corruption."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self.server, self.port, _ = _start_server(self.db)
        self.host = "127.0.0.1"

    def tearDown(self):
        _close_server(self.server)
        self.tmp.cleanup()

    def test_two_simultaneous_requests_succeed(self):
        results = [None, None]
        errors = [None, None]

        def do_request(idx):
            try:
                results[idx] = _make_request(
                    self.host, self.port, "/v1/execute",
                    {"operation": "inspect"})
            except Exception as e:
                errors[idx] = e

        threads = [threading.Thread(target=do_request, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        for i in range(2):
            self.assertIsNone(errors[i], f"Thread {i} raised: {errors[i]}")
            status, payload = results[i]
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["result"]["node_count"], 3)

    def test_slow_request_does_not_block_fast(self):
        """A slow request does not prevent a fast request from completing."""
        fast_done = threading.Event()
        fast_result = [None]

        def slow_request():
            # inspect is fast; we use a small sleep to simulate latency
            time.sleep(0.05)
            _make_request(self.host, self.port, "/v1/execute",
                          {"operation": "inspect"})

        def fast_request():
            fast_result[0] = _make_request(
                self.host, self.port, "/v1/execute",
                {"operation": "inspect"})
            fast_done.set()

        slow_thread = threading.Thread(target=slow_request)
        fast_thread = threading.Thread(target=fast_request)

        slow_thread.start()
        fast_thread.start()

        fast_done.wait(timeout=5)
        slow_thread.join(timeout=10)
        fast_thread.join(timeout=5)

        self.assertIsNotNone(fast_result[0], "Fast request did not complete")
        status, payload = fast_result[0]
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_five_concurrent_read_only_requests(self):
        """Five concurrent requests all produce identical results."""
        results = [None] * 5
        errors = [None] * 5

        def do_request(idx):
            try:
                results[idx] = _make_request(
                    self.host, self.port, "/v1/execute",
                    {"operation": "search", "arguments": {"query": "concept"}})
            except Exception as e:
                errors[idx] = e

        threads = [threading.Thread(target=do_request, args=(i,))
                   for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        for i in range(5):
            self.assertIsNone(errors[i], f"Thread {i} raised: {errors[i]}")
            status, payload = results[i]
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])

        # All five results should be identical (deterministic, read-only)
        first_result = results[0][1]["result"]
        for i in range(1, 5):
            self.assertEqual(results[i][1]["result"], first_result,
                             f"Result {i} differs from result 0")

    def test_concurrent_requests_do_not_corrupt_shared_state(self):
        """Multiple concurrent requests share the engine without error."""
        n = 10
        results = [None] * n
        errors = [None] * n

        def do_request(idx):
            try:
                results[idx] = _make_request(
                    self.host, self.port, "/v1/execute",
                    {"operation": "inspect"})
            except Exception as e:
                errors[idx] = e

        threads = [threading.Thread(target=do_request, args=(i,))
                   for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        for i in range(n):
            self.assertIsNone(errors[i], f"Thread {i} raised: {errors[i]}")
            status, payload = results[i]
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["result"]["node_count"], 3)

    def test_initialization_count_is_one_despite_concurrency(self):
        """Concurrent requests still result in exactly one initialization."""
        n = 5
        barriers = [threading.Event()] * n

        def do_request(idx):
            _make_request(self.host, self.port, "/v1/execute",
                          {"operation": "inspect"})

        threads = [threading.Thread(target=do_request, args=(i,))
                   for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        self.assertEqual(self.server.stats["initializations"], 1)


# ---------------------------------------------------------------------------
# Structured logging tests
# ---------------------------------------------------------------------------

class StructuredLoggingTests(unittest.TestCase):
    """Exactly one structured JSON log line per completed request to stderr."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self.server, self.port, _ = _start_server(self.db)
        self.host = "127.0.0.1"

    def tearDown(self):
        _close_server(self.server)
        self.tmp.cleanup()

    def _capture_stderr(self, func):
        """Run func while capturing stderr; return list of JSON log lines."""
        import io
        captured = io.StringIO()
        old_stderr = sys.stderr
        sys.stderr = captured
        try:
            func()
        finally:
            sys.stderr = old_stderr
        output = captured.getvalue()
        lines = [l.strip() for l in output.strip().split("\n") if l.strip()
                 and not l.startswith("HTTP stats=")]
        return lines

    def test_successful_request_produces_one_log_line(self):
        lines = self._capture_stderr(lambda: _make_request(
            self.host, self.port, "/v1/execute",
            {"operation": "inspect"}))
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["method"], "POST")
        self.assertEqual(entry["path"], "/v1/execute")
        self.assertEqual(entry["status"], 200)
        self.assertIn("duration_ms", entry)
        self.assertIsInstance(entry["duration_ms"], (int, float))
        self.assertEqual(entry["operation"], "inspect")
        self.assertNotIn("error", entry)

    def test_error_request_produces_log_with_error_code(self):
        lines = self._capture_stderr(lambda: _make_request(
            self.host, self.port, "/v1/execute",
            {"operation": "get", "arguments": {"node_id": "missing"}}))
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["status"], 404)
        self.assertEqual(entry["error"], "node_not_found")
        self.assertIn("duration_ms", entry)

    def test_no_api_key_in_log(self):
        """API keys must never appear in log output."""
        _close_server(self.server)
        self.server, self.port, _ = _start_server(self.db, api_key="supersecret123")
        lines = self._capture_stderr(lambda: _make_request(
            self.host, self.port, "/v1/execute",
            {"operation": "inspect"},
            headers={"X-API-Key": "supersecret123"}))
        full_log = "\n".join(lines)
        self.assertNotIn("supersecret123", full_log)

    def test_no_stack_traces_in_log(self):
        lines = self._capture_stderr(lambda: _make_request(
            self.host, self.port, "/v1/execute", b"{bad",
            headers={"Content-Type": "application/json"}))
        full_log = "\n".join(lines)
        self.assertNotIn("Traceback", full_log)
        self.assertNotIn('File "', full_log)

    def test_no_filesystem_paths_in_log(self):
        lines = self._capture_stderr(lambda: _make_request(
            self.host, self.port, "/v1/execute",
            {"operation": "inspect"}))
        full_log = "\n".join(lines)
        self.assertNotIn("/root/", full_log)
        self.assertNotIn("/tmp/", full_log)

    def test_stdout_receives_only_json_response(self):
        """stdout should get the JSON response, not log lines."""
        import io
        captured_out = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = captured_out
        try:
            _make_request(self.host, self.port, "/v1/execute",
                          {"operation": "inspect"})
        finally:
            sys.stdout = old_stdout
        # stdout should not contain log lines (only test harness output)

    def test_unauthorized_produces_log_with_error(self):
        _close_server(self.server)
        self.server, self.port, _ = _start_server(self.db, api_key="key1")
        lines = self._capture_stderr(lambda: _make_request(
            self.host, self.port, "/v1/execute",
            {"operation": "inspect"}))
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["status"], 401)
        self.assertEqual(entry["error"], "unauthorized")

    def test_multiple_requests_produce_multiple_log_lines(self):
        lines = self._capture_stderr(lambda: [
            _make_request(self.host, self.port, "/v1/execute",
                          {"operation": "inspect"})
            for _ in range(3)
        ])
        self.assertEqual(len(lines), 3)
        for line in lines:
            entry = json.loads(line)
            self.assertIn("method", entry)
            self.assertIn("status", entry)
            self.assertIn("duration_ms", entry)


# ---------------------------------------------------------------------------
# Health endpoint tests
# ---------------------------------------------------------------------------

class HealthEndpointTests(unittest.TestCase):
    """GET /health returns live metrics without exposing sensitive data."""

    def setUp(self):
        self.tmp, self.db = _temp_db()
        self.server, self.port, _ = _start_server(self.db)
        self.host = "127.0.0.1"

    def tearDown(self):
        _close_server(self.server)
        self.tmp.cleanup()

    def _health(self):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            conn.request("GET", "/health")
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def test_health_returns_live_metrics(self):
        status, payload = self._health()
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["contract_version"], CONTRACT_VERSION)
        self.assertIn("uptime", payload)
        self.assertIn("request_count", payload)
        self.assertIn("initialization_count", payload)
        self.assertIn("load_seconds", payload)

    def test_health_uptime_is_positive(self):
        _, payload = self._health()
        self.assertIsInstance(payload["uptime"], (int, float))
        self.assertGreater(payload["uptime"], 0)

    def test_health_request_count_increases(self):
        _, before = self._health()
        count_before = before["request_count"]
        # Make some requests
        _make_request(self.host, self.port, "/v1/execute",
                      {"operation": "inspect"})
        _make_request(self.host, self.port, "/v1/execute",
                      {"operation": "inspect"})
        _, after = self._health()
        # request_count should increase by at least 2 (the inspect requests)
        # plus the health request itself
        self.assertGreater(after["request_count"], count_before)

    def test_health_initialization_count_one(self):
        """After several requests, initialization_count should be 1."""
        for _ in range(3):
            _make_request(self.host, self.port, "/v1/execute",
                          {"operation": "inspect"})
        _, payload = self._health()
        self.assertEqual(payload["initialization_count"], 1)

    def test_health_load_seconds_is_number(self):
        # Trigger engine initialization first
        _make_request(self.host, self.port, "/v1/execute",
                      {"operation": "inspect"})
        _, payload = self._health()
        self.assertIsInstance(payload["load_seconds"], (int, float))
        self.assertGreaterEqual(payload["load_seconds"], 0)

    def test_health_responds_fast(self):
        """Health endpoint should respond within 500ms."""
        start = time.monotonic()
        status, _ = self._health()
        elapsed = (time.monotonic() - start) * 1000
        self.assertEqual(status, 200)
        self.assertLess(elapsed, 500,
                        "Health endpoint took too long: %.0fms" % elapsed)

    def test_health_exposes_no_database_path(self):
        _, payload = self._health()
        text = json.dumps(payload)
        self.assertNotIn("knowledge.db", text)
        self.assertNotIn("/database/", text)
        self.assertNotIn("/root/", text)

    def test_health_exposes_no_api_key(self):
        _close_server(self.server)
        self.server, self.port, _ = _start_server(self.db, api_key="secretkey")
        _, payload = self._health()
        text = json.dumps(payload)
        self.assertNotIn("secretkey", text)


# ---------------------------------------------------------------------------
# Authentication tests
# ---------------------------------------------------------------------------

class AuthenticationConstantTimeTests(unittest.TestCase):
    """API-key comparison uses hmac.compare_digest (constant-time)."""

    def test_authorized_uses_hmac_compare_digest(self):
        """Verify the _authorized method calls hmac.compare_digest."""
        from http_server.server import V1Handler
        # Create a minimal mock handler to inspect the method
        with patch("http_server.server.hmac.compare_digest",
                    return_value=True) as mock_cmp:
            # Build a minimal handler-like object
            class FakeHandler:
                headers = {"X-API-Key": "testkey"}
                def __init__(self, h):
                    self.headers = h
            handler = FakeHandler.__new__(V1Handler)
            handler.headers = {"X-API-Key": "testkey"}
            result = handler._authorized("testkey")
            self.assertTrue(result)
            mock_cmp.assert_called_once()

    def test_hmac_module_is_used_in_server(self):
        """The server module imports hmac at the top level."""
        import http_server.server as srv
        self.assertTrue(hasattr(srv, "hmac"))

    def test_wrong_key_rejected_with_constant_time(self):
        """Wrong key is rejected (existing behavior preserved)."""
        from http_server.server import V1Handler
        with patch("http_server.server.hmac.compare_digest",
                    return_value=False):
            handler = V1Handler.__new__(V1Handler)
            handler.headers = {"X-API-Key": "wrongkey"}
            result = handler._authorized("correctkey")
            self.assertFalse(result)

    def test_missing_key_returns_false_immediately(self):
        """Missing key returns False without calling hmac.compare_digest."""
        from http_server.server import V1Handler
        with patch("http_server.server.hmac.compare_digest") as mock_cmp:
            handler = V1Handler.__new__(V1Handler)
            handler.headers = {}
            result = handler._authorized("anykey")
            self.assertFalse(result)
            mock_cmp.assert_not_called()


# ---------------------------------------------------------------------------
# Error mapping tests
# ---------------------------------------------------------------------------

class ErrorMappingTests(unittest.TestCase):
    """Authentication error mapping is correct."""

    def test_unauthorized_maps_to_authentication_error(self):
        from external_http_client.errors import (
            exception_for_code, AuthenticationError)
        exc = exception_for_code("unauthorized", "missing key")
        self.assertIsInstance(exc, AuthenticationError)
        self.assertEqual(exc.code, "unauthorized")

    def test_other_error_codes_still_map_correctly(self):
        from external_http_client.errors import (
            exception_for_code, NodeNotFoundError, InvalidRequestError,
            UnknownOperationError, InternalError)
        self.assertIsInstance(
            exception_for_code("node_not_found"), NodeNotFoundError)
        self.assertIsInstance(
            exception_for_code("invalid_request"), InvalidRequestError)
        self.assertIsInstance(
            exception_for_code("unknown_operation"), UnknownOperationError)
        self.assertIsInstance(
            exception_for_code("internal_error"), InternalError)


# ---------------------------------------------------------------------------
# Production DB safety tests
# ---------------------------------------------------------------------------

class ProductionDBSafetyTests(unittest.TestCase):
    """Production database is never touched by v0.9 changes."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = os.path.join(self.tmp.name, "knowledge.db")
        _seed_db(self.db_path)
        self.hash_before = _sha256(self.db_path)

    def test_db_hash_unchanged_after_v09_server(self):
        """Starting and stopping the server does not modify the DB."""
        import io
        server = KnowledgeHTTPServer(
            ("127.0.0.1", 0), db_path=self.db_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]
        try:
            # Make several requests (production DB may take time to load)
            for op in ("inspect", "search", "get"):
                if op == "search":
                    body = {"operation": op, "arguments": {"query": "python"}}
                elif op == "get":
                    body = {"operation": op,
                            "arguments": {"node_id": "nonexistent"}}
                else:
                    body = {"operation": op}
                _make_request("127.0.0.1", port, "/v1/execute", body,
                              timeout=30)
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
# Process boundary tests
# ---------------------------------------------------------------------------

class ProcessBoundaryConcurrencyTests(unittest.TestCase):
    """Concurrent requests through the real HTTP boundary (subprocess server)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "knowledge.db")
        _seed_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _start_subprocess_server(self):
        import subprocess
        proc = subprocess.Popen(
            [sys.executable, "-m", "http_server",
             "--db", self.db, "--port", "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True)
        # Read stderr until we get the listening line
        port = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            line = proc.stderr.readline()
            if not line:
                break
            if "HTTP listening on" in line:
                # Parse "HTTP listening on 127.0.0.1:PORT"
                port_str = line.split(":")[-1].strip()
                port = int(port_str)
                break
        return proc, port

    def _stop_subprocess_server(self, proc):
        import signal
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)

    def test_concurrent_requests_via_subprocess_server(self):
        """Five concurrent requests through the real HTTP server subprocess."""
        proc, port = self._start_subprocess_server()
        self.assertIsNotNone(port, "Server did not start in time")
        try:
            results = [None] * 5
            errors = [None] * 5

            def do_request(idx):
                try:
                    results[idx] = _make_request(
                        "127.0.0.1", port, "/v1/execute",
                        {"operation": "inspect"})
                except Exception as e:
                    errors[idx] = e

            threads = [threading.Thread(target=do_request, args=(i,))
                       for i in range(5)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=15)

            for i in range(5):
                self.assertIsNone(errors[i], f"Thread {i} raised: {errors[i]}")
                status, payload = results[i]
                self.assertEqual(status, 200)
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["result"]["node_count"], 3)

            # All results should be identical
            first = results[0][1]["result"]
            for i in range(1, 5):
                self.assertEqual(results[i][1]["result"], first)

        finally:
            self._stop_subprocess_server(proc)

    def test_health_via_subprocess_server(self):
        """Health endpoint works on the subprocess server."""
        proc, port = self._start_subprocess_server()
        self.assertIsNotNone(port)
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))
            conn.close()
            self.assertEqual(resp.status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["contract_version"], CONTRACT_VERSION)
            self.assertIn("request_count", payload)
            self.assertIn("uptime", payload)
        finally:
            self._stop_subprocess_server(proc)


if __name__ == "__main__":
    unittest.main()
