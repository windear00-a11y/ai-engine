"""Phase 8 — Public Memory Contract / API v2.

Tests for:
  * v1 remains byte/behavior compatible (contract_version 1, 6 ops)
  * v2 remember/recall end-to-end via MemoryClient InProcess and via HTTP
  * invalid v2 requests rejected deterministically
  * project isolation preserved via v2
  * provenance/context survives contract boundary
  * MemoryClient works against v2
  * existing Phase 0-7 + API/HTTP regressions still pass
"""

import os
import sys
import tempfile
import unittest
import json
import time
import threading
import http.client
import socket

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

EXPECTED_V1_OPS = ("search", "get", "related", "follow", "provenance", "inspect")
EXPECTED_V2_OPS = ("remember", "recall", "get", "provenance", "inspect",
                   "context.get",
                   "lifecycle.ingest", "lifecycle.experience",
                   "lifecycle.learning", "lifecycle.strategy",
                   "lifecycle.trace", "lifecycle.describe",
                   "lifecycle.summary", "lifecycle.plan",
                   "lifecycle.grant", "lifecycle.authorize",
                   "lifecycle.execute")


def _free_port():
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class V1CompatibilityTests(unittest.TestCase):
    def test_v1_contract_still_frozen(self):
        from api.contract import CONTRACT_VERSION, OPERATIONS
        self.assertEqual(CONTRACT_VERSION, "1")
        self.assertEqual(tuple(OPERATIONS), EXPECTED_V1_OPS)
        # v1 file must be byte-identical to pre-Phase8 (no edit)
        # Check that v1 contract file still has 6 ops and not v2 ops
        self.assertNotIn("remember", OPERATIONS)
        self.assertNotIn("recall", OPERATIONS)

    def test_v1_behavior_unchanged(self):
        from api.tools import ToolInterface
        import tempfile
        from retrieval.repository import KnowledgeRepository
        tmp = tempfile.mkdtemp()
        db = os.path.join(tmp, "k.db")
        repo = KnowledgeRepository(db)
        repo.initialize()
        sid = repo.add_source("test")
        repo.add_node("n1", "concept", "N1", "desc hello", source_id=sid)
        repo.close()
        iface = ToolInterface(db_path=db)
        res = iface.execute({"operation": "search", "arguments": {"query": "hello"}})
        self.assertTrue(res["ok"])
        self.assertEqual(res["contract_version"], "1")
        self.assertIn("result", res)
        iface.close()
        import shutil
        shutil.rmtree(tmp)

    def test_v1_and_v2_versions_distinct(self):
        from api.contract import CONTRACT_VERSION as V1
        from api.contract_v2 import CONTRACT_VERSION as V2
        self.assertEqual(V1, "1")
        self.assertEqual(V2, "2")
        self.assertNotEqual(V1, V2)

    def test_v1_http_still_works(self):
        from http_server.server import KnowledgeHTTPServer
        import tempfile, json, http.client, threading, time
        with tempfile.TemporaryDirectory() as tmp:
            # Create tiny v1 DB
            from retrieval.repository import KnowledgeRepository
            db = os.path.join(tmp, "k.db")
            repo = KnowledgeRepository(db)
            repo.initialize()
            sid = repo.add_source("test")
            repo.add_node("n1", "concept", "N1", "hello world", source_id=sid)
            repo.close()
            port = _free_port()
            server = KnowledgeHTTPServer(("127.0.0.1", port), db_path=db)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            time.sleep(0.5)
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                payload = json.dumps({"operation": "search", "arguments": {"query": "hello"}}).encode()
                conn.request("POST", "/v1/execute", body=payload, headers={"Content-Type": "application/json", "Content-Length": str(len(payload))})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode())
                self.assertTrue(data["ok"])
                self.assertEqual(data["contract_version"], "1")
                conn.close()
                # Health should still report v1
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                h = json.loads(resp.read().decode())
                self.assertEqual(h["contract_version"], "1")
                self.assertIn("contract_version_v2", h)
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=5)


class V2ContractTests(unittest.TestCase):
    def test_v2_operations_defined(self):
        from api.contract_v2 import CONTRACT_VERSION, OPERATIONS
        self.assertEqual(CONTRACT_VERSION, "2")
        self.assertEqual(set(OPERATIONS.keys()), set(EXPECTED_V2_OPS))

    def test_v2_validation_rejects_unknown(self):
        from api.memory_tools import MemoryToolInterface
        iface = MemoryToolInterface(data_root=tempfile.mkdtemp())
        res = iface.execute({"operation": "unknown_op", "arguments": {}})
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "unknown_operation")
        self.assertEqual(res["contract_version"], "2")
        iface.close()

    def test_v2_validation_rejects_invalid_args(self):
        from api.memory_tools import MemoryToolInterface
        with tempfile.TemporaryDirectory() as tmp:
            iface = MemoryToolInterface(data_root=tmp)
            # remember without text -> invalid_argument
            res = iface.execute({"operation": "remember", "arguments": {}})
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"]["code"], "invalid_argument")
            # recall without query
            res2 = iface.execute({"operation": "recall", "arguments": {}})
            self.assertFalse(res2["ok"])
            self.assertEqual(res2["error"]["code"], "invalid_argument")
            # extra arg should be rejected
            res3 = iface.execute({"operation": "recall", "arguments": {"query": "hi", "sql": "DROP"}})
            self.assertFalse(res3["ok"])
            self.assertEqual(res3["error"]["code"], "invalid_argument")
            iface.close()

    def test_v2_deterministic_rejection(self):
        from api.memory_tools import MemoryToolInterface
        with tempfile.TemporaryDirectory() as tmp:
            iface = MemoryToolInterface(data_root=tmp)
            r1 = iface.execute({"operation": "remember", "arguments": {}})
            r2 = iface.execute({"operation": "remember", "arguments": {}})
            self.assertEqual(r1, r2)
            iface.close()


class V2E2ETests(unittest.TestCase):
    def test_remember_recall_via_memory_client_inprocess(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        with tempfile.TemporaryDirectory() as tmp:
            transport = MemoryInProcessTransport(data_root=tmp)
            client = MemoryClient(transport)
            # remember — generic payload
            res = client.remember(payload={"text": "v2 e2e fact about sleep", "type": "fact"}, project_id="default")
            self.assertIn("node_id", res)
            self.assertIn("activity_id", res)
            self.assertIn("context_id", res)
            # recall
            out = client.recall(query="sleep", project_id="default")
            self.assertGreaterEqual(len(out["knowledge"]), 1)
            self.assertTrue(any("sleep" in n["description"].lower() for n in out["knowledge"]))
            # provenance survives
            prov = client.provenance(node_id=res["node_id"], project_id="default")
            self.assertEqual(prov["node_id"], res["node_id"])
            self.assertIn("source_name", prov)
            # context survives
            ctx = client.context_get(context_id=res["context_id"], project_id="default")
            self.assertEqual(ctx["context_id"], res["context_id"])
            self.assertIn("project", ctx)
            client.close()

    def test_structured_payload_works(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        with tempfile.TemporaryDirectory() as tmp:
            client = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            # Structured payload without explicit text field but with custom structured data
            payload = {"type": "fact", "name": "custom fact", "description": "structured payload description", "custom_field": {"a": 42}}
            res = client.remember(payload=payload, project_id="default")
            self.assertIn("node_id", res)
            out = client.recall(query="structured payload", project_id="default")
            self.assertGreaterEqual(len(out["knowledge"]), 1)
            client.close()

    def test_text_inside_payload(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        with tempfile.TemporaryDirectory() as tmp:
            client = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            # Text can still be represented inside payload as {"text": "..."}
            res = client.remember(payload={"text": "text inside payload fact", "type": "fact"}, project_id="default")
            self.assertIn("node_id", res)
            out = client.recall(query="text inside payload", project_id="default")
            self.assertTrue(any("text inside payload" in n["description"] for n in out["knowledge"]))
            client.close()

    def test_non_text_structured_payload(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        with tempfile.TemporaryDirectory() as tmp:
            client = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            # Non-text structured payload (no text field, arbitrary JSON)
            payload = {"type": "observation", "name": "obs1", "description": "non-text structured", "extra_data": {"x": 1, "y": [2, 3]}}
            res = client.remember(payload=payload, project_id="default")
            self.assertIn("node_id", res)
            out = client.recall(query="non-text structured", project_id="default")
            self.assertGreaterEqual(len(out["knowledge"]), 1)
            client.close()

    def test_missing_payload_rejected(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        from knowledge_client.errors import InvalidArgumentError
        with tempfile.TemporaryDirectory() as tmp:
            client = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            with self.assertRaises(InvalidArgumentError):
                client.request("remember", {"project_id": "default"})  # missing payload
            with self.assertRaises(InvalidArgumentError):
                client.request("remember", {"payload": "not a dict", "project_id": "default"})
            client.close()

    def test_remember_recall_via_http(self):
        from http_server.server import KnowledgeHTTPServer
        import http.client, json, threading, time
        with tempfile.TemporaryDirectory() as tmp:
            data_root = os.path.join(tmp, "data")
            port = _free_port()
            server = KnowledgeHTTPServer(("127.0.0.1", port), db_path=os.path.join(tmp, "k.db"), data_root=data_root)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            time.sleep(0.5)
            try:
                # remember via v2 — generic payload
                payload = json.dumps({"operation": "remember", "arguments": {"payload": {"text": "http v2 fact", "type": "fact"}, "project_id": "default"}}).encode()
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/v2/execute", body=payload, headers={"Content-Type": "application/json", "Content-Length": str(len(payload))})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode())
                self.assertTrue(data["ok"], data)
                self.assertEqual(data["contract_version"], "2")
                node_id = data["result"]["node_id"]
                conn.close()
                # recall via v2
                payload2 = json.dumps({"operation": "recall", "arguments": {"query": "http v2 fact", "project_id": "default"}}).encode()
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/v2/execute", body=payload2, headers={"Content-Type": "application/json", "Content-Length": str(len(payload2))})
                resp2 = conn.getresponse()
                data2 = json.loads(resp2.read().decode())
                self.assertTrue(data2["ok"], data2)
                self.assertGreaterEqual(len(data2["result"]["knowledge"]), 1)
                conn.close()
                # v1 still works
                payload_v1 = json.dumps({"operation": "inspect"}).encode()
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/v1/execute", body=payload_v1, headers={"Content-Type": "application/json", "Content-Length": str(len(payload_v1))})
                resp_v1 = conn.getresponse()
                d_v1 = json.loads(resp_v1.read().decode())
                self.assertEqual(d_v1["contract_version"], "1")
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=5)

    def test_project_isolation_via_v2(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        with tempfile.TemporaryDirectory() as tmp:
            c1 = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            c2 = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            r1 = c1.remember(payload={"text": "project A isolated fact", "type": "fact"}, project_id="proj_a")
            r2 = c2.remember(payload={"text": "project B isolated fact", "type": "fact"}, project_id="proj_b")
            self.assertNotEqual(r1["node_id"], r2["node_id"])
            out_a = c1.recall(query="project A isolated", project_id="proj_a")
            out_b = c2.recall(query="project B isolated", project_id="proj_b")
            self.assertTrue(any("project A" in n["description"] for n in out_a["knowledge"]))
            self.assertFalse(any("project B" in n["description"] for n in out_a["knowledge"]))
            self.assertTrue(any("project B" in n["description"] for n in out_b["knowledge"]))
            c1.close()
            c2.close()

    def test_provenance_context_survive_v2(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        with tempfile.TemporaryDirectory() as tmp:
            client = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            res = client.remember(payload={"text": "provenance context test", "type": "fact"}, project_id="default")
            nid = res["node_id"]
            cid = res["context_id"]
            # provenance
            prov = client.provenance(node_id=nid, project_id="default")
            self.assertEqual(prov["node_id"], nid)
            self.assertIn("source_name", prov)
            # context
            ctx = client.context_get(context_id=cid, project_id="default")
            self.assertEqual(ctx["context_id"], cid)
            self.assertIn("environment", ctx)
            self.assertIn("project", ctx)
            self.assertIn("source", ctx)
            # recall knowledge should have provenance
            out = client.recall(query="provenance context test", project_id="default")
            self.assertTrue(any(n.get("provenance", {}).get("source_name") == "manual" for n in out["knowledge"]))
            client.close()

    def test_invalid_v2_requests_rejected(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        from knowledge_client.errors import InvalidArgumentError, UnknownOperationError
        with tempfile.TemporaryDirectory() as tmp:
            client = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            with self.assertRaises(InvalidArgumentError):
                client.remember(payload={}, project_id="default")
            with self.assertRaises(InvalidArgumentError):
                client.remember(payload="not a dict", project_id="default")
            with self.assertRaises(InvalidArgumentError):
                client.recall(query="", project_id="default")
            with self.assertRaises(UnknownOperationError):
                client.request("unknown_op_xyz", {})
            client.close()

    def test_memory_client_v2_interface(self):
        from knowledge_client import MemoryClient, MemoryInProcessTransport, CONTRACT_VERSION_V2
        self.assertEqual(CONTRACT_VERSION_V2, "2")
        with tempfile.TemporaryDirectory() as tmp:
            transport = MemoryInProcessTransport(data_root=tmp)
            client = MemoryClient(transport)
            # Check transport
            self.assertIs(client.transport, transport)
            # Remember via client — generic payload
            res = client.remember(payload={"text": "client interface test", "type": "fact"}, project_id="default")
            self.assertIn("activity_id", res)
            client.close()

    def test_invariants_still_hold(self):
        from api.contract import CONTRACT_VERSION as V1
        from api.contract_v2 import CONTRACT_VERSION as V2
        from tools.permissions.policy import HARD_WRITE_INVARIANTS
        import intelligence
        self.assertEqual(V1, "1")
        self.assertEqual(V2, "2")
        self.assertEqual(list(HARD_WRITE_INVARIANTS), [("database/knowledge.db", "blocked"), ("database/knowledge.db.backup", "blocked")])
        self.assertEqual(intelligence.__version__, "0")
        # No domain runtimes: the generic core has no built-in coding tools.
        from ai_engine.registry import AdapterRegistry
        reg = AdapterRegistry()
        self.assertFalse(reg.has_effect("file.write"))
        self.assertFalse(reg.has_effect("knowledge.search"))


if __name__ == "__main__":
    unittest.main()
