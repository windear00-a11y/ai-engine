"""Phase 12 — MemoryClient v2 SDK hardened.

Tests for (12):
1. InProcess MemoryClient
2. Session/subprocess transport
3. HTTP MemoryClient
4. identical request semantics across transports
5. typed success results
6. validation errors
7. unknown operation/error mapping
8. project isolation
9. provenance
10. context retrieval
11. inspect
12. v1 KnowledgeClient regression
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

LEGACY_DB = os.path.join(_ROOT, "database", "knowledge.db")

def _free_port():
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port

def _wait_health(host, port, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            conn = http.client.HTTPConnection(host, port, timeout=2)
            conn.request("GET", "/health")
            resp = conn.getresponse()
            data = json.loads(resp.read().decode())
            conn.close()
            if data.get("ok"):
                return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


class InProcessTests(unittest.TestCase):
    def test_inprocess_remember_recall(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        with tempfile.TemporaryDirectory() as tmp:
            client = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            res = client.remember(payload={"text": "inprocess test", "type": "fact"}, project_id="default")
            self.assertIn("node_id", res)
            out = client.recall(query="inprocess test", project_id="default")
            self.assertGreaterEqual(len(out["knowledge"]), 1)
            client.close()

    def test_typed_success_results(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        with tempfile.TemporaryDirectory() as tmp:
            client = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            res = client.remember(payload={"text": "typed test", "type": "fact"}, project_id="default")
            # Typed: should have activity_id, context_id, node_id
            self.assertIsInstance(res["node_id"], str)
            self.assertIsInstance(res["activity_id"], str)
            self.assertIsInstance(res["context_id"], str)
            out = client.recall(query="typed test", project_id="default")
            self.assertIsInstance(out["knowledge"], list)
            self.assertIsInstance(out["query_terms"], list)
            client.close()


class SessionTests(unittest.TestCase):
    def test_session_transport(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemorySessionTransport
        with tempfile.TemporaryDirectory() as tmp:
            transport = MemorySessionTransport(data_root=tmp)
            client = MemoryClient(transport)
            res = client.remember(payload={"text": "session test fact", "type": "fact"}, project_id="default")
            self.assertIn("node_id", res)
            out = client.recall(query="session test", project_id="default")
            self.assertGreaterEqual(len(out["knowledge"]), 1)
            client.close()

    def test_session_identical_semantics(self):
        # Same logical operation via InProcess vs Session should give same node_id for same payload (deterministic)
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport, MemorySessionTransport
        with tempfile.TemporaryDirectory() as tmp1, tempfile.TemporaryDirectory() as tmp2:
            # Use same data_root for both? For identical semantics, we need same project and same payload -> same deterministic ids
            # But they are isolated data_roots, so node_id should be same (deterministic from content) even though stored separately
            c1 = MemoryClient(MemoryInProcessTransport(data_root=tmp1))
            c2 = MemoryClient(MemorySessionTransport(data_root=tmp2))
            payload = {"text": "identical semantics test", "type": "fact"}
            r1 = c1.remember(payload=payload, project_id="default")
            r2 = c2.remember(payload=payload, project_id="default")
            self.assertEqual(r1["node_id"], r2["node_id"])
            c1.close()
            c2.close()


class HttpTests(unittest.TestCase):
    def test_http_remember_recall(self):
        from http_server.server import KnowledgeHTTPServer
        from knowledge_client import MemoryClient
        from knowledge_client.transports import HttpMemoryTransport
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            port = _free_port()
            server = KnowledgeHTTPServer(("127.0.0.1", port), db_path=os.path.join(tmp, "k.db"), data_root=tmp)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                self.assertTrue(_wait_health("127.0.0.1", port, timeout=5))
                client = MemoryClient(HttpMemoryTransport(host="127.0.0.1", port=port))
                res = client.remember(payload={"text": "http test fact", "type": "fact"}, project_id="default")
                self.assertIn("node_id", res)
                out = client.recall(query="http test fact", project_id="default")
                self.assertGreaterEqual(len(out["knowledge"]), 1)
                client.close()
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=5)

    def test_http_identical_semantics(self):
        from http_server.server import KnowledgeHTTPServer
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport, HttpMemoryTransport
        import threading
        with tempfile.TemporaryDirectory() as tmp:
            port = _free_port()
            server = KnowledgeHTTPServer(("127.0.0.1", port), db_path=os.path.join(tmp, "k.db"), data_root=tmp)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                _wait_health("127.0.0.1", port)
                # InProcess
                c_in = MemoryClient(MemoryInProcessTransport(data_root=tmp))
                payload = {"text": "identical http test", "type": "fact"}
                r_in = c_in.remember(payload=payload, project_id="default")
                c_in.close()
                # HTTP (different data_root? Use same tmp so same project DB, but http will see same stored node, so recall should find it)
                # For identical semantics, we test that Http recall finds the same node_id as InProcess recall
                c_http = MemoryClient(HttpMemoryTransport(host="127.0.0.1", port=port))
                out_http = c_http.recall(query="identical http test", project_id="default")
                # Should find the node created via InProcess (same DB)
                self.assertTrue(any(n["id"] == r_in["node_id"] for n in out_http["knowledge"]))
                c_http.close()
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=5)


class ValidationErrorTests(unittest.TestCase):
    def test_validation_errors(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        from knowledge_client.errors import InvalidArgumentError
        with tempfile.TemporaryDirectory() as tmp:
            client = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            with self.assertRaises(InvalidArgumentError):
                client.remember(payload="not a dict", project_id="default")
            with self.assertRaises(InvalidArgumentError):
                client.remember(payload={}, project_id="default")  # empty payload -> invalid
            with self.assertRaises(InvalidArgumentError):
                client.recall(query="", project_id="default")
            client.close()

    def test_unknown_operation_error_mapping(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        from knowledge_client.errors import UnknownOperationError
        with tempfile.TemporaryDirectory() as tmp:
            client = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            with self.assertRaises(UnknownOperationError):
                client.request("unknown_op_xyz", {})
            client.close()


class ProjectIsolationTests(unittest.TestCase):
    def test_project_isolation(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        with tempfile.TemporaryDirectory() as tmp:
            c1 = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            c2 = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            c1.remember(payload={"text": "project A fact"}, project_id="proj_a")
            c2.remember(payload={"text": "project B fact"}, project_id="proj_b")
            out_a = c1.recall(query="project A fact", project_id="proj_a")
            out_b = c2.recall(query="project B fact", project_id="proj_b")
            self.assertTrue(any("project A" in n["description"] for n in out_a["knowledge"]))
            self.assertFalse(any("project B" in n["description"] for n in out_a["knowledge"]))
            c1.close()
            c2.close()


class ProvenanceTests(unittest.TestCase):
    def test_provenance(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        with tempfile.TemporaryDirectory() as tmp:
            client = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            res = client.remember(payload={"text": "provenance test fact", "type": "fact"}, project_id="default")
            nid = res["node_id"]
            prov = client.provenance(node_id=nid, project_id="default")
            self.assertEqual(prov["node_id"], nid)
            self.assertIn("source_name", prov)
            client.close()


class ContextRetrievalTests(unittest.TestCase):
    def test_context_get(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        with tempfile.TemporaryDirectory() as tmp:
            client = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            res = client.remember(payload={"text": "context test", "type": "fact"}, project_id="default")
            cid = res["context_id"]
            ctx = client.context_get(context_id=cid, project_id="default")
            self.assertEqual(ctx["context_id"], cid)
            self.assertIn("environment", ctx)
            self.assertIn("project", ctx)
            client.close()


class InspectTests(unittest.TestCase):
    def test_inspect(self):
        from knowledge_client import MemoryClient
        from knowledge_client.transports import MemoryInProcessTransport
        with tempfile.TemporaryDirectory() as tmp:
            client = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            client.remember(payload={"text": "inspect test"}, project_id="default")
            info = client.inspect(project_id="default")
            self.assertIn("node_count", info)
            self.assertGreaterEqual(info["node_count"], 1)
            self.assertIn("project_id", info)
            client.close()


class V1RegressionTests(unittest.TestCase):
    def test_v1_still_works(self):
        from knowledge_client import KnowledgeClient
        from knowledge_client.transports import InProcessTransport, SessionTransport
        import tempfile
        from retrieval.repository import KnowledgeRepository
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            repo = KnowledgeRepository(db)
            repo.initialize()
            sid = repo.add_source("test")
            repo.add_node("n1", "concept", "N1", "hello world", source_id=sid)
            repo.close()
            # InProcess
            client = KnowledgeClient(InProcessTransport(db_path=db))
            res = client.search("hello")
            self.assertGreaterEqual(len(res), 1)
            client.close()
            # Session
            client2 = KnowledgeClient(SessionTransport(db=db))
            res2 = client2.search("hello")
            self.assertGreaterEqual(len(res2), 1)
            client2.close()


if __name__ == "__main__":
    unittest.main()
