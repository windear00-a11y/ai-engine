"""Minimal HTTP transport over the frozen Knowledge Engine Public Contract v1.

This module is a *transport only*: every HTTP request is converted into a
Contract v1 request dict and handed to the existing validation + execution
boundary (:class:`api.tools.ToolInterface`). The response envelope (including
``contract_version``) is preserved verbatim. No knowledge logic lives here.

Design notes
------------
* stdlib ``http.server`` only -- no FastAPI/Flask/Django.
* Thread-per-request via ``ThreadingHTTPServer`` for concurrent request
  handling; each request runs in its own daemon thread.
* The engine (repository + knowledge API) is loaded lazily ONCE and reused
  across requests; never reloaded per request.
* Optional API-key check at the transport boundary (never part of the
  Contract). Invalid/missing credentials are rejected before the engine sees
  the request.  Uses constant-time comparison (``hmac.compare_digest``) to
  prevent timing side-channel attacks.
* Structured JSON request logging to stderr -- one line per completed request.
* A transport-level ``GET /health`` returns live metrics (uptime, request
  count, initialization count, load time).
* Minimal CORS support: ``OPTIONS`` preflight returns 200 with appropriate
  headers; all JSON responses carry ``Access-Control-Allow-Origin: *`` so
  browser clients (e.g. the static Web App) can read responses cross-origin.
  API-key authentication is enforced on ``POST`` regardless of origin.
"""

import hmac
import json
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from api.contract import CONTRACT_VERSION
from api.tools import ToolInterface
from retrieval.repository import DEFAULT_KNOWLEDGE_DB

__all__ = [
    "MAX_BODY_BYTES",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "HTTP_STATUS_FOR_CODE",
    "DEFAULT_STATUS",
    "KnowledgeHTTPServer",
    "V1Handler",
    "_single_env",
]

MAX_BODY_BYTES = 1 << 20          # 1 MiB transport cap on the request body
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# HTTP status for each frozen Contract v1 error code. The JSON error code
# inside the envelope REMAINS authoritative; this mapping is a thin transport
# convenience so a client can choose a status-based fast path.
HTTP_STATUS_FOR_CODE = {
    "invalid_request": 400,
    "unknown_operation": 404,
    "invalid_argument": 400,
    "invalid_relationship_type": 400,
    "node_not_found": 404,
    "internal_error": 500,
}
DEFAULT_STATUS = 500  # fallback for unexpected codes (still structured JSON)


def _single_env(code, message, operation=None):
    """Build a contract-shaped error envelope without touching the engine."""
    return {
        "ok": False,
        "operation": operation,
        "contract_version": CONTRACT_VERSION,
        "error": {"code": code, "message": message},
    }


class _CountingFactory:
    """Reference factory used by tests to prove single initialization."""

    def __init__(self, db_path):
        self.db_path = db_path
        self.calls = 0

    def __call__(self, db_path=None):
        self.calls += 1
        return ToolInterface(db_path=db_path or self.db_path)


class KnowledgeHTTPServer(ThreadingHTTPServer):
    """Thread-per-request HTTP server sharing one lazily-loaded engine.

    Each incoming request is handled in its own daemon thread.  The
    repository/API is loaded on the first ``/v1/execute`` request and reused
    for every subsequent request; it is never reloaded per request.

    Structured JSON request logs are written to stderr (one line per completed
    request).  ``GET /health`` returns live server metrics.
    """

    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, db_path=DEFAULT_KNOWLEDGE_DB, api_key=None,
                 max_body_bytes=MAX_BODY_BYTES, interface_factory=None):
        self.db_path = db_path
        self.api_key = api_key
        self.max_body_bytes = max_body_bytes
        # interface_factory(db) -> object with .execute(dict) and .close().
        # Tests inject a counting factory to prove initialization happens once.
        self._factory = interface_factory or self._make_thread_safe_factory()
        self._interface = None
        self._load_failed = False
        self._closing = False
        self._stats_written = False
        self._serving_thread = None
        self._stats_lock = threading.Lock()
        self._init_lock = threading.Lock()
        self._execute_lock = threading.Lock()
        self._start_time = time.monotonic()
        self.stats = {
            "requests": 0,          # POST /v1/execute requests processed
            "initializations": 0,   # times the repository was loaded
            "load_seconds": None,   # wall time of the (single) repository load
        }
        super().__init__(addr, V1Handler)

    @staticmethod
    def _make_thread_safe_factory():
        """Create a factory that opens SQLite with check_same_thread=False.

        This is required for ThreadingHTTPServer where each request runs in
        a new thread but the engine is created once and shared.
        """
        from api.knowledge_api import KnowledgeAPI
        from retrieval.knowledge import KnowledgeStore
        from retrieval.repository import KnowledgeRepository

        def factory(db_path):
            repo = KnowledgeRepository(db_path, check_same_thread=False)
            store = KnowledgeStore(repository=repo)
            api = KnowledgeAPI(db_path=None, store=store)
            return ToolInterface(api=api)

        return factory

    # -- engine lifecycle (lazy, single load) ------------------------------

    def _ensure_loaded(self):
        """Load the engine once; thread-safe via double-checked locking."""
        if self._interface is not None:
            return self._interface
        if self._load_failed:
            return None
        with self._init_lock:
            if self._interface is not None:
                return self._interface
            if self._load_failed:
                return None
            try:
                start = time.monotonic()
                self._interface = self._factory(self.db_path)
            except Exception:  # noqa: BLE001 - cache failure; no retry storm
                self._load_failed = True
                return None
            self.stats["load_seconds"] = time.monotonic() - start
            self.stats["initializations"] += 1
            return self._interface

    def execute(self, request):
        """Route one HTTP request into the existing Contract v1 boundary.

        Never raises: any failure becomes a structured ``ok: false`` envelope
        with the stable error code, matching the tool contract exactly.

        Requests are serialized through ``_execute_lock`` because the shared
        ``ToolInterface`` / SQLite connection is not thread-safe.
        """
        with self._stats_lock:
            self.stats["requests"] += 1
        interface = self._ensure_loaded()
        if interface is None:
            return _single_env(
                "internal_error", "server could not initialize the engine")
        with self._execute_lock:
            return interface.execute(request)

    def health_response(self):
        """Build a live-metrics health envelope (outside Contract v1)."""
        with self._stats_lock:
            requests = self.stats["requests"]
            initializations = self.stats["initializations"]
            load_seconds = self.stats["load_seconds"]
        uptime = time.monotonic() - self._start_time
        return {
            "ok": True,
            "contract_version": CONTRACT_VERSION,
            "uptime": round(uptime, 3),
            "request_count": requests,
            "initialization_count": initializations,
            "load_seconds": load_seconds,
        }

    def close(self):
        """Release the shared engine (idempotent; thread-safe).

        The interface owns an SQLite connection, which may only be closed
        from the thread that created it (the serving thread). If we are not
        that thread, we ask the serving thread to do it and wait for it.
        """
        if self._closing:
            return
        self._closing = True
        if threading.current_thread() is self._serving_thread:
            self._close_engine_in_thread()
        elif self._serving_thread is not None:
            self.shutdown()  # serve_forever returns; it closes the engine
            self._serving_thread.join(timeout=5)
        else:
            # Never served; nothing was ever created.
            self._write_stats()

    def _close_engine_in_thread(self):
        """Close the engine; must run in the thread that owns the SQLite conn."""
        if self._interface is not None:
            try:
                self._interface.close()
            except Exception:  # noqa: BLE001 - close must not raise
                pass
            self._interface = None
        self._write_stats()

    def _write_stats(self):
        if self._stats_written:
            return
        self._stats_written = True
        try:
            sys.stderr.write(
                "HTTP stats=%s end\n" % json.dumps(
                    self.stats, ensure_ascii=False, sort_keys=True))
            sys.stderr.flush()
        except (OSError, ValueError):  # stderr already closed
            pass

    def serve_forever(self, poll_interval=0.5):
        """Serve forever; on shutdown, release the engine in this thread."""
        self._serving_thread = threading.current_thread()
        try:
            super().serve_forever(poll_interval)
        finally:
            self._close_engine_in_thread()


class V1Handler(BaseHTTPRequestHandler):
    """Handle transport-level concerns; delegate all semantics to the engine.

    Transport layer responsibilities ONLY: method/path routing, content-type
    checks, request-size bounds, optional API-key gate, JSON parsing, and
    structured envelope serialization. No knowledge logic here.
    """

    server_version = "KnowledgeEngineHTTP/1.0"
    protocol_version = "HTTP/1.1"
    # Short socket timeout so an idle keep-alive connection does not block
    # shutdown() forever: the serve loop wakes at least this often and can see
    # the shutdown request.
    timeout = 0.25
    _CONTRACT_PATH = "/v1/execute"
    _HEALTH_PATH = "/health"

    # -- helpers -----------------------------------------------------------

    @property
    def _engine(self) -> KnowledgeHTTPServer:
        """Typed access to the shared engine server instance."""
        server = self.server
        assert isinstance(server, KnowledgeHTTPServer)
        return server

    def log_message(self, format: str, *args):
        """Keep default BaseHTTPRequestHandler logging quiet."""
        return

    def handle_error(self, request, client_address):
        """Suppress per-request tracebacks (timeouts on idle connections)."""
        return

    def _log_request(self, method, path, status, duration_ms, operation=None,
                     error_code=None):
        """Write one structured JSON line to stderr for a completed request."""
        entry = {
            "method": method,
            "path": path,
            "status": status,
            "duration_ms": round(duration_ms, 2),
        }
        if operation is not None:
            entry["operation"] = operation
        if error_code is not None:
            entry["error"] = error_code
        try:
            sys.stderr.write(json.dumps(entry, ensure_ascii=False) + "\n")
            sys.stderr.flush()
        except (OSError, ValueError):
            pass

    def _send_cors_headers(self):
        """Add CORS headers to the current response."""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def _send_bytes(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, status, payload):
        self._send_bytes(
            status, json.dumps(payload, ensure_ascii=False, sort_keys=True)
            .encode("utf-8"))

    def _authorized(self, expected_key):
        supplied = self.headers.get("X-API-Key") or ""
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer "):
            supplied = auth[len("bearer "):].strip()
        if not supplied:
            return False
        return hmac.compare_digest(supplied.encode("utf-8"),
                                   expected_key.encode("utf-8"))

    def _status_for(self, envelope):
        if envelope.get("ok") is True:
            return 200
        code = (envelope.get("error") or {}).get("code")
        code = str(code) if isinstance(code, str) else None
        if code is not None:
            return HTTP_STATUS_FOR_CODE.get(code, DEFAULT_STATUS)
        return DEFAULT_STATUS

    # -- routing -----------------------------------------------------------

    def do_GET(self):
        if self.path == self._HEALTH_PATH:
            self._send_json(200, self._engine.health_response())
            return
        if self.path == self._CONTRACT_PATH:
            self._reject_method()
            return
        self._send_json(
            404, _single_env("invalid_request", "unknown endpoint (see /health)"))

    def do_POST(self):
        server = self._engine
        start = time.monotonic()
        method = "POST"
        path = self.path
        operation = None
        error_code = None
        status = 200

        if self.path != self._CONTRACT_PATH:
            status = 404
            error_code = "invalid_request"
            self._send_json(
                status, _single_env("invalid_request",
                                    "unknown endpoint (use %s)" % self._CONTRACT_PATH))
            duration_ms = (time.monotonic() - start) * 1000
            self._log_request(method, path, status, duration_ms,
                              error_code=error_code)
            return

        if server.api_key and not self._authorized(server.api_key):
            status = 401
            error_code = "unauthorized"
            self._send_json(
                status, _single_env("unauthorized", "missing or invalid API key"))
            duration_ms = (time.monotonic() - start) * 1000
            self._log_request(method, path, status, duration_ms,
                              error_code=error_code)
            return

        content_type = (self.headers.get("Content-Type", "") or "").split(";")[0]
        if content_type.strip().lower() != "application/json":
            status = 415
            error_code = "invalid_request"
            self._send_json(
                status, _single_env("invalid_request", "unsupported content type"))
            duration_ms = (time.monotonic() - start) * 1000
            self._log_request(method, path, status, duration_ms,
                              error_code=error_code)
            return

        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = -1
        if length <= 0:
            status = 411
            error_code = "invalid_request"
            self._send_json(
                status, _single_env("invalid_request", "missing Content-Length"))
            duration_ms = (time.monotonic() - start) * 1000
            self._log_request(method, path, status, duration_ms,
                              error_code=error_code)
            return
        if length > server.max_body_bytes:
            status = 413
            error_code = "invalid_request"
            self._send_json(
                status, _single_env("invalid_request", "request body too large"))
            duration_ms = (time.monotonic() - start) * 1000
            self._log_request(method, path, status, duration_ms,
                              error_code=error_code)
            return

        try:
            raw = self.rfile.read(length)
        except OSError as exc:
            status = 400
            error_code = "invalid_request"
            self._send_json(
                status, _single_env("invalid_request",
                                    "could not read body: %s" % exc))
            duration_ms = (time.monotonic() - start) * 1000
            self._log_request(method, path, status, duration_ms,
                              error_code=error_code)
            return

        try:
            request = json.loads(raw)
        except ValueError:
            status = 400
            error_code = "invalid_request"
            self._send_json(
                status, _single_env("invalid_request", "invalid JSON body"))
            duration_ms = (time.monotonic() - start) * 1000
            self._log_request(method, path, status, duration_ms,
                              error_code=error_code)
            return

        envelope = server.execute(request)  # never raises
        operation = request.get("operation") if isinstance(request, dict) else None
        status = self._status_for(envelope)
        if not envelope.get("ok"):
            error_code = (envelope.get("error") or {}).get("code")
        self._send_json(status, envelope)
        duration_ms = (time.monotonic() - start) * 1000
        self._log_request(method, path, status, duration_ms,
                          operation=operation, error_code=error_code)

    # -- unsupported methods ----------------------------------------------

    def _reject_method(self):
        self._send_json(
            405, _single_env("invalid_request", "method not allowed"))

    def do_OPTIONS(self):
        """Handle CORS preflight: return 200 with required headers, no body."""
        self.send_response(200)
        self._send_cors_headers()
        self.end_headers()

    do_PUT = _reject_method
    do_DELETE = _reject_method
    do_PATCH = _reject_method
    do_HEAD = _reject_method