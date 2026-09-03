"""HTTP transport for Intelligence API (Phase 10).

Mirrors http_server/server.py pattern but for intelligence read-only operations.
"""

import json
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .contract import INTELLIGENCE_CONTRACT_VERSION
from .handler import IntelligenceToolInterface


class IntelligenceHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, interface_factory=None, max_body_bytes=1 << 20):
        self.max_body_bytes = max_body_bytes
        self._factory = interface_factory or (lambda: IntelligenceToolInterface())
        self._interface = None
        self._init_lock = threading.Lock()
        self._execute_lock = threading.Lock()
        self._start_time = time.monotonic()
        self.stats = {"requests": 0, "initializations": 0, "load_seconds": None}
        super().__init__(addr, IntelligenceHTTPHandler)

    def _ensure_loaded(self):
        if self._interface is not None:
            return self._interface
        with self._init_lock:
            if self._interface is not None:
                return self._interface
            start = time.monotonic()
            self._interface = self._factory()
            self.stats["load_seconds"] = time.monotonic() - start
            self.stats["initializations"] += 1
            return self._interface

    def execute(self, request):
        self.stats["requests"] += 1
        iface = self._ensure_loaded()
        with self._execute_lock:
            return iface.execute(request)


class IntelligenceHTTPHandler(BaseHTTPRequestHandler):
    _CONTRACT_PATH = "/v1/intelligence"
    _HEALTH_PATH = "/health"

    def log_message(self, format, *args):
        return

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == self._HEALTH_PATH:
            self._send_json(200, {"ok": True, "contract_version": INTELLIGENCE_CONTRACT_VERSION})
            return
        self._send_json(404, {"ok": False, "error": {"code": "invalid_request", "message": "use POST /v1/intelligence"}})

    def do_POST(self):
        if self.path != self._CONTRACT_PATH:
            self._send_json(404, {"ok": False, "error": {"code": "invalid_request", "message": "unknown endpoint"}})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > self.server.max_body_bytes:
            self._send_json(400, {"ok": False, "error": {"code": "invalid_request", "message": "bad Content-Length"}})
            return
        try:
            raw = self.rfile.read(length)
            request = json.loads(raw)
        except Exception:
            self._send_json(400, {"ok": False, "error": {"code": "invalid_request", "message": "invalid JSON"}})
            return
        envelope = self.server.execute(request)
        status = 200 if envelope.get("ok") else 400
        if envelope.get("error", {}).get("code") == "not_found":
            status = 404
        self._send_json(status, envelope)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    do_PUT = do_GET
    do_DELETE = do_GET
    do_PATCH = do_GET
