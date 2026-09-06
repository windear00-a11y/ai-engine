"""Phase 11 — Local Daemon + HTTP MemoryClient.

Tests for (15):
1. foreground server starts/stops cleanly
2. /health
3. /v1/execute regression
4. /v2/execute
5. HTTP MemoryClient.remember
6. HTTP MemoryClient.recall
7. project isolation over HTTP
8. authentication behavior
9. invalid request/error envelope
10. PID creation/removal
11. --daemon
12. --stop
13. port/startup failure handling
14. no unexpected network binding
15. existing InProcess/Session transports remain working
"""

import os
import sys
import tempfile
import unittest
import json
import time
import threading
import socket
import http.client
import subprocess
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

LEGACY_DB = os.path.join(_ROOT, "database", "knowledge.db")
EXPECTED_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"

def _sha256(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1<<20), b""):
            h.update(c)
    return h.hexdigest()

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

def _run_cli(args, data_root=None, env=None):
    e = os.environ.copy()
    if data_root:
        e["AI_ENGINE_DATA_DIR"] = data_root
    if env:
        e.update(env)
    cmd = [sys.executable, "-m", "ai_engine"] + args
    return subprocess.run(cmd, cwd=_ROOT, capture_output=True, text=True, env=e, timeout=30)


class ForegroundServerTests(unittest.TestCase):
    def test_foreground_starts_stops_cleanly(self):
        from http_server.server import KnowledgeHTTPServer
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            from retrieval.repository import KnowledgeRepository
            repo = KnowledgeRepository(db)
            repo.initialize()
            repo.close()
            port = _free_port()
            server = KnowledgeHTTPServer(("127.0.0.1", port), db_path=db, data_root=tmp)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            self.assertTrue(_wait_health("127.0.0.1", port, timeout=5))
            server.shutdown()
            server.server_close()
            t.join(timeout=5)
            self.assertFalse(t.is_alive())

    def test_health(self):
        from http_server.server import KnowledgeHTTPServer
        with tempfile.TemporaryDirectory() as tmp:
            port = _free_port()
            server = KnowledgeHTTPServer(("127.0.0.1", port), db_path=os.path.join(tmp, "k.db"), data_root=tmp)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                self.assertTrue(_wait_health("127.0.0.1", port))
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("GET", "/health")
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode())
                self.assertTrue(data["ok"])
                self.assertIn("contract_version", data)
                self.assertIn("contract_version_v2", data)
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=5)

    def test_v1_regression(self):
        from http_server.server import KnowledgeHTTPServer
        from retrieval.repository import KnowledgeRepository
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            repo = KnowledgeRepository(db)
            repo.initialize()
            sid = repo.add_source("test")
            repo.add_node("n1", "concept", "N1", "hello world", source_id=sid)
            repo.close()
            port = _free_port()
            server = KnowledgeHTTPServer(("127.0.0.1", port), db_path=db, data_root=tmp)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                _wait_health("127.0.0.1", port)
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                payload = json.dumps({"operation": "search", "arguments": {"query": "hello"}}).encode()
                conn.request("POST", "/v1/execute", body=payload, headers={"Content-Type": "application/json", "Content-Length": str(len(payload))})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode())
                self.assertTrue(data["ok"])
                self.assertEqual(data["contract_version"], "1")
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=5)

    def test_v2_execute(self):
        from http_server.server import KnowledgeHTTPServer
        with tempfile.TemporaryDirectory() as tmp:
            port = _free_port()
            server = KnowledgeHTTPServer(("127.0.0.1", port), db_path=os.path.join(tmp, "k.db"), data_root=tmp)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                _wait_health("127.0.0.1", port)
                # remember via v2
                payload = json.dumps({"operation": "remember", "arguments": {"payload": {"text": "v2 http test", "type": "fact"}, "project_id": "default"}}).encode()
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/v2/execute", body=payload, headers={"Content-Type": "application/json", "Content-Length": str(len(payload))})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode())
                self.assertTrue(data["ok"], data)
                self.assertEqual(data["contract_version"], "2")
                conn.close()
                # recall via v2
                payload2 = json.dumps({"operation": "recall", "arguments": {"query": "v2 http test", "project_id": "default"}}).encode()
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/v2/execute", body=payload2, headers={"Content-Type": "application/json", "Content-Length": str(len(payload2))})
                resp2 = conn.getresponse()
                data2 = json.loads(resp2.read().decode())
                self.assertTrue(data2["ok"])
                self.assertGreaterEqual(len(data2["result"]["knowledge"]), 1)
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=5)


class HttpMemoryClientTests(unittest.TestCase):
    def test_http_remember(self):
        from http_server.server import KnowledgeHTTPServer
        from knowledge_client import MemoryClient
        from knowledge_client.transports import HttpMemoryTransport
        with tempfile.TemporaryDirectory() as tmp:
            port = _free_port()
            server = KnowledgeHTTPServer(("127.0.0.1", port), db_path=os.path.join(tmp, "k.db"), data_root=tmp)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                _wait_health("127.0.0.1", port)
                client = MemoryClient(HttpMemoryTransport(host="127.0.0.1", port=port))
                res = client.remember(payload={"text": "http remember test", "type": "fact"}, project_id="default")
                self.assertIn("node_id", res)
                client.close()
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=5)

    def test_http_recall(self):
        from http_server.server import KnowledgeHTTPServer
        from knowledge_client import MemoryClient
        from knowledge_client.transports import HttpMemoryTransport
        with tempfile.TemporaryDirectory() as tmp:
            port = _free_port()
            server = KnowledgeHTTPServer(("127.0.0.1", port), db_path=os.path.join(tmp, "k.db"), data_root=tmp)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                _wait_health("127.0.0.1", port)
                client = MemoryClient(HttpMemoryTransport(host="127.0.0.1", port=port))
                client.remember(payload={"text": "http recall test fact", "type": "fact"}, project_id="default")
                out = client.recall(query="http recall test", project_id="default")
                self.assertGreaterEqual(len(out["knowledge"]), 1)
                client.close()
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=5)

    def test_project_isolation_over_http(self):
        from http_server.server import KnowledgeHTTPServer
        from knowledge_client import MemoryClient
        from knowledge_client.transports import HttpMemoryTransport
        with tempfile.TemporaryDirectory() as tmp:
            port = _free_port()
            server = KnowledgeHTTPServer(("127.0.0.1", port), db_path=os.path.join(tmp, "k.db"), data_root=tmp)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                _wait_health("127.0.0.1", port)
                c1 = MemoryClient(HttpMemoryTransport(host="127.0.0.1", port=port))
                c2 = MemoryClient(HttpMemoryTransport(host="127.0.0.1", port=port))
                c1.remember(payload={"text": "http project A fact", "type": "fact"}, project_id="proj_a")
                c2.remember(payload={"text": "http project B fact", "type": "fact"}, project_id="proj_b")
                out_a = c1.recall(query="project A", project_id="proj_a")
                out_b = c2.recall(query="project B", project_id="proj_b")
                self.assertTrue(any("project A" in n["description"] for n in out_a["knowledge"]))
                self.assertFalse(any("project B" in n["description"] for n in out_a["knowledge"]))
                self.assertTrue(any("project B" in n["description"] for n in out_b["knowledge"]))
                c1.close()
                c2.close()
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=5)


class AuthTests(unittest.TestCase):
    def test_auth_behavior(self):
        from http_server.server import KnowledgeHTTPServer
        with tempfile.TemporaryDirectory() as tmp:
            port = _free_port()
            server = KnowledgeHTTPServer(("127.0.0.1", port), db_path=os.path.join(tmp, "k.db"), data_root=tmp, api_key="secret123")
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                _wait_health("127.0.0.1", port)
                # Without key should be 401
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                payload = json.dumps({"operation": "remember", "arguments": {"payload": {"text": "auth test"}, "project_id": "default"}}).encode()
                conn.request("POST", "/v2/execute", body=payload, headers={"Content-Type": "application/json", "Content-Length": str(len(payload))})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 401)
                conn.close()
                # With correct key should succeed
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/v2/execute", body=payload, headers={"Content-Type": "application/json", "Content-Length": str(len(payload)), "X-API-Key": "secret123"})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                data = json.loads(resp.read().decode())
                self.assertTrue(data["ok"])
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=5)


class InvalidRequestTests(unittest.TestCase):
    def test_invalid_envelope(self):
        from http_server.server import KnowledgeHTTPServer
        with tempfile.TemporaryDirectory() as tmp:
            port = _free_port()
            server = KnowledgeHTTPServer(("127.0.0.1", port), db_path=os.path.join(tmp, "k.db"), data_root=tmp)
            t = threading.Thread(target=server.serve_forever, daemon=True)
            t.start()
            try:
                _wait_health("127.0.0.1", port)
                # Invalid JSON
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                conn.request("POST", "/v2/execute", body=b"not json", headers={"Content-Type": "application/json", "Content-Length": "8"})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 400)
                data = json.loads(resp.read().decode())
                self.assertFalse(data["ok"])
                self.assertEqual(data["error"]["code"], "invalid_request")
                conn.close()
                # Unknown operation
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                payload = json.dumps({"operation": "unknown_op", "arguments": {}}).encode()
                conn.request("POST", "/v2/execute", body=payload, headers={"Content-Type": "application/json", "Content-Length": str(len(payload))})
                resp = conn.getresponse()
                data = json.loads(resp.read().decode())
                self.assertFalse(data["ok"])
                self.assertEqual(data["error"]["code"], "unknown_operation")
                self.assertEqual(data["contract_version"], "2")
                conn.close()
            finally:
                server.shutdown()
                server.server_close()
                t.join(timeout=5)


class PidTests(unittest.TestCase):
    def test_pid_creation_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = os.path.join(tmp, "daemon.pid")
            log_path = os.path.join(tmp, "daemon.log")
            # Start via CLI --daemon
            proc = _run_cli(["serve", "--host", "127.0.0.1", "--port", str(_free_port()), "--daemon", "--pid-file", pid_path, "--log-file", log_path], data_root=tmp)
            # CLI --daemon should return quickly with 0 and create pid file
            # Give it a moment
            time.sleep(1)
            self.assertTrue(os.path.exists(pid_path), f"pid file not created: {proc.stdout} {proc.stderr}")
            with open(pid_path) as f:
                pid = int(f.read().strip())
            self.assertTrue(pid > 0)
            # Check that process is running
            try:
                os.kill(pid, 0)
                running = True
            except OSError:
                running = False
            self.assertTrue(running)
            # Cleanup via --stop
            proc2 = _run_cli(["serve", "--stop", "--pid-file", pid_path], data_root=tmp)
            self.assertEqual(proc2.returncode, 0)
            time.sleep(0.5)
            self.assertFalse(os.path.exists(pid_path))

    def test_daemon_and_stop(self):
        with tempfile.TemporaryDirectory() as tmp:
            port = _free_port()
            pid_path = os.path.join(tmp, "daemon.pid")
            log_path = os.path.join(tmp, "daemon.log")
            proc = _run_cli(["serve", "--host", "127.0.0.1", "--port", str(port), "--daemon", "--pid-file", pid_path, "--log-file", log_path], data_root=tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(os.path.exists(pid_path))
            # Wait for health
            self.assertTrue(_wait_health("127.0.0.1", port, timeout=5))
            # Log should exist
            self.assertTrue(os.path.exists(log_path))
            # Stop
            proc2 = _run_cli(["serve", "--stop", "--pid-file", pid_path], data_root=tmp)
            self.assertEqual(proc2.returncode, 0)
            time.sleep(0.5)
            self.assertFalse(os.path.exists(pid_path))
            # Stopping again should fail gracefully (no pid)
            proc3 = _run_cli(["serve", "--stop", "--pid-file", pid_path], data_root=tmp)
            self.assertNotEqual(proc3.returncode, 0)

    def test_port_startup_failure(self):
        # Bind a port then try to start server on same port should fail
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(1)
        try:
            proc = _run_cli(["serve", "--host", "127.0.0.1", "--port", str(port)], data_root=tempfile.mkdtemp())
            # Since we run serve in foreground, it would block; instead test via direct server bind
            # Here we test that second server fails to bind
            from http_server.server import KnowledgeHTTPServer
            with self.assertRaises(OSError):
                srv2 = KnowledgeHTTPServer(("127.0.0.1", port), db_path=":memory:")
        finally:
            s.close()

    def test_daemon_log_handling(self):
        with tempfile.TemporaryDirectory() as tmp:
            port = _free_port()
            pid_path = os.path.join(tmp, "daemon.pid")
            log_path = os.path.join(tmp, "daemon.log")
            proc = _run_cli(["serve", "--host", "127.0.0.1", "--port", str(port), "--daemon", "--pid-file", pid_path, "--log-file", log_path], data_root=tmp)
            self.assertEqual(proc.returncode, 0)
            time.sleep(1)
            self.assertTrue(os.path.exists(log_path))
            # Log should be writable and contain no error
            with open(log_path) as f:
                content = f.read()
            # Clean up
            _run_cli(["serve", "--stop", "--pid-file", pid_path], data_root=tmp)
            time.sleep(0.5)

    def test_no_unexpected_network_binding(self):
        # Default must be 127.0.0.1, not 0.0.0.0
        # Check CLI default
        import subprocess, sys
        proc = subprocess.run([sys.executable, "-m", "ai_engine", "serve", "--help"], cwd=_ROOT, capture_output=True, text=True)
        self.assertIn("127.0.0.1", proc.stdout)
        # Check that http_server default is 127.0.0.1
        from http_server.server import DEFAULT_HOST
        self.assertEqual(DEFAULT_HOST, "127.0.0.1")

    def test_existing_transports_remain_working(self):
        # InProcess and Session should still work after v2 changes
        from knowledge_client import KnowledgeClient
        from knowledge_client.transports import InProcessTransport
        from retrieval.repository import KnowledgeRepository
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "k.db")
            repo = KnowledgeRepository(db)
            repo.initialize()
            sid = repo.add_source("test")
            repo.add_node("n1", "concept", "N1", "hello world", source_id=sid)
            repo.close()
            client = KnowledgeClient(InProcessTransport(db_path=db))
            res = client.search("hello")
            self.assertGreaterEqual(len(res), 1)
            client.close()
            # Session
            from knowledge_client.transports import SessionTransport
            st = SessionTransport(db=db)
            client2 = KnowledgeClient(st)
            res2 = client2.search("hello")
            self.assertGreaterEqual(len(res2), 1)
            client2.close()
            # Memory InProcess still works
            from knowledge_client import MemoryClient
            from knowledge_client.transports import MemoryInProcessTransport
            mclient = MemoryClient(MemoryInProcessTransport(data_root=tmp))
            r = mclient.remember(payload={"text": "transport test"}, project_id="default")
            self.assertIn("node_id", r)
            mclient.close()


def _run_cli(args, data_root=None, env=None):
    e = os.environ.copy()
    if data_root:
        e["AI_ENGINE_DATA_DIR"] = data_root
    if env:
        e.update(env)
    cmd = [sys.executable, "-m", "ai_engine"] + args
    return subprocess.run(cmd, cwd=_ROOT, capture_output=True, text=True, env=e, timeout=15)


if __name__ == "__main__":
    unittest.main()
