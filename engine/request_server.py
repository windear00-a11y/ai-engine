"""HTTP transport for AI Engine v1 Request → Verified Work (AI Engine v1).

Separate from Contract v1 (/v1/execute). Uses stdlib http.server, thread-per-request,
reuses RequestHandler. No approved=true bypass — approval is server-side only.
"""

import json
import time
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from engine.request_handler import handle_request


class RequestHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr, workspace_root=None, stores=None, operator_approval=None, max_body_bytes=1 << 20):
        self.workspace_root = workspace_root
        self.stores = stores
        self.operator_approval = operator_approval
        self.max_body_bytes = max_body_bytes
        self._start_time = time.monotonic()
        self.stats = {"requests": 0}
        super().__init__(addr, RequestHTTPHandler)

    def execute(self, request_dict):
        self.stats["requests"] += 1
        # Do NOT strip — let RequestContract reject forbidden keys as invalid_argument (no bypass)
        return handle_request(request_dict, workspace_root=self.workspace_root, stores=self.stores, operator_approval=self.operator_approval)


class RequestHTTPHandler(BaseHTTPRequestHandler):
    _REQUEST_PATH = "/v1/request"
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
            self._send_json(200, {"ok": True, "service": "ai-engine-v1", "requests": self.server.stats["requests"]})
            return
        self._send_json(404, {"ok": False, "error": {"code": "invalid_request", "message": "use POST /v1/request"}})

    def do_POST(self):
        if self.path != self._REQUEST_PATH:
            self._send_json(404, {"ok": False, "error": {"code": "invalid_request", "message": "unknown endpoint (use /v1/request)"}})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0 or length > self.server.max_body_bytes:
            self._send_json(400, {"ok": False, "error": {"code": "invalid_request", "message": "bad Content-Length"}})
            return
        ctype = (self.headers.get("Content-Type", "") or "").split(";")[0].strip().lower()
        if ctype != "application/json":
            self._send_json(415, {"ok": False, "error": {"code": "invalid_request", "message": "unsupported content type"}})
            return
        try:
            raw = self.rfile.read(length)
            request = json.loads(raw)
        except Exception:
            self._send_json(400, {"ok": False, "error": {"code": "invalid_request", "message": "invalid JSON"}})
            return
        result = self.server.execute(request)
        # Status mapping
        if result.get("ok"):
            status = 200
        elif result.get("status") == "awaiting_approval":
            status = 202
        elif result.get("error", {}).get("code") in ("not_found",):
            status = 404
        else:
            status = 400
        self._send_json(status, result)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    do_PUT = do_GET
    do_DELETE = do_GET
    do_PATCH = do_GET
