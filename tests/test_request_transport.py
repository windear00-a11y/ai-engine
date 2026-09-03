"""V1: HTTP transport for Request → Verified Work."""

import json, threading, time, unittest
from http.client import HTTPConnection
from engine.request_server import RequestHTTPServer

class RequestTransportTests(unittest.TestCase):
    def setUp(self):
        self.server = RequestHTTPServer(("127.0.0.1", 0), workspace_root="/tmp")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        time.sleep(0.2)
        self.port = self.server.server_address[1]

    def tearDown(self):
        try:
            self.server.shutdown()
            self.thread.join(timeout=2)
            self.server.server_close()
        except Exception:
            pass

    def _post(self, payload):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = json.dumps(payload).encode("utf-8")
        conn.request("POST", "/v1/request", body, headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data

    def test_transport_success(self):
        status, data = self._post({"request": "Fix E302 in src/utils.py"})
        self.assertEqual(status, 200)
        self.assertIn("intent", data)
        self.assertEqual(data["intent"]["intent"], "bug_fix")

    def test_transport_invalid_request(self):
        status, data = self._post({"request": "   "})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_transport_approved_bypass_rejected(self):
        status, data = self._post({"request": "Fix E302 in src/utils.py", "approved": True})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"]["code"], "invalid_argument")

    def test_health(self):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertTrue(data["ok"])
