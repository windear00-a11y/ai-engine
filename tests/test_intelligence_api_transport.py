"""Phase 10: intelligence API HTTP transport."""

import json
import threading
import time
import unittest
from http.client import HTTPConnection

from intelligence.api.server import IntelligenceHTTPServer
from intelligence.api.handler import IntelligenceToolInterface
from intelligence.experience.store import ExperienceStore
from intelligence.experience.schema import ExperienceRecord, derive_experience_id


class IntelligenceAPITransportTests(unittest.TestCase):
    def setUp(self):
        exp_store = ExperienceStore(":memory:", check_same_thread=False)
        rec = ExperienceRecord(
            experience_id=derive_experience_id("t1", "ctx_a", "oc1", ["ev1"], "st1"),
            task_id="t1", task_type="bug_fix", domain="lint", context_id="ctx_a",
            outcome_id="oc1", evidence_ids=("ev1",), strategy_id="st1",
            summary={}, synthesized_at_epoch=1.0)
        exp_store.save(rec)
        self.exp_id = rec.experience_id
        self.server = IntelligenceHTTPServer(
            ("127.0.0.1", 0),
            interface_factory=lambda: IntelligenceToolInterface(experience_store=exp_store))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        # Wait for server to be ready
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
        conn.request("POST", "/v1/intelligence", body,
                     headers={"Content-Type": "application/json", "Content-Length": str(len(body))})
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        return resp.status, data

    def test_transport_success(self):
        status, data = self._post({"operation": "experience.get",
                                   "arguments": {"experience_id": self.exp_id}})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["result"]["experience_id"], self.exp_id)

    def test_transport_not_found(self):
        status, data = self._post({"operation": "experience.get",
                                   "arguments": {"experience_id": "nonexistent"}})
        self.assertEqual(status, 404)
        self.assertFalse(data["ok"])

    def test_transport_unknown_operation(self):
        status, data = self._post({"operation": "unknown.op", "arguments": {}})
        self.assertEqual(status, 400)
        self.assertFalse(data["ok"])

    def test_health_endpoint(self):
        conn = HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/health")
        resp = conn.getresponse()
        data = json.loads(resp.read().decode("utf-8"))
        conn.close()
        self.assertEqual(resp.status, 200)
        self.assertTrue(data["ok"])
