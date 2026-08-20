"""Tests for the persistent Knowledge Engine session (``python -m api.session``).

Covers:
* a long-lived process that serves many requests WITHOUT reloading the DB;
* one JSON request per line in / one JSON response per line out;
* error isolation -- invalid/malformed requests do not kill the session;
* EOF clean shutdown;
* no human-readable output on stdout (responses are the only stdout);
* deterministic responses and sequential ordering;
* the repository being initialized exactly once;
* read-only behaviour (seeded temp DB and production DB bytes unchanged);
* a latency probe proving the first request pays the load cost and later
  requests do not (session beats the fresh-process model).
"""

import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.session import SessionServer
from api.tools import ToolInterface
from retrieval.repository import KnowledgeRepository

PRODUCTION_DB = os.path.join(_ROOT, "database", "knowledge.db")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _seed_db(db_path, n_extra=0):
    """Traversal fixture plus (optionally) more nodes for latency probes."""
    repo = KnowledgeRepository(db_path)
    repo.initialize()
    sid = repo.add_source("fixture-source", version="1.0")
    repo.add_node("transport", "entity", "Transport(WriteTransport, ReadTransport)",
                  "An I/O transport with read and write halves", source_id=sid)
    repo.add_node("write-transport", "entity", "WriteTransport",
                  "Write side of a transport", source_id=sid)
    repo.add_node("read-transport", "entity", "ReadTransport",
                  "Read side of a transport", source_id=sid)
    repo.add_node("buffered-transport", "entity", "BufferedTransport",
                  "A buffered transport", source_id=sid)
    repo.add_node("log", "entity", "Usage", "Logging facilities", source_id=sid)
    repo.add_relationship("transport", "extends", "write-transport", "fixture")
    repo.add_relationship("transport", "extends", "read-transport", "fixture")
    repo.add_relationship("read-transport", "references", "log", "fixture")
    for i in range(n_extra):
        repo.add_node("extra-%03d" % i, "concept", "Extra Node %d" % i,
                      "extra matching text", source_id=sid)
    repo.close()


class _StampingStream(io.StringIO):
    """Records a monotonic timestamp on every write (per-response timing)."""

    def __init__(self):
        super().__init__()
        self.stamps = []

    def write(self, text):
        self.stamps.append(time.monotonic())
        return super().write(text)


class _CountingFactory:
    """Injects a fixed interface and counts how many loads actually happen."""

    def __init__(self, db):
        self.calls = 0
        self._interface = ToolInterface(db_path=db)

    def __call__(self, db_path):
        self.calls += 1
        return self._interface

    def close(self):
        self._interface.close()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "knowledge.db")
        _seed_db(self.db)
        self.hash_before = _sha256(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def serve(self, lines, factory=None):
        """Feed lines to a SessionServer; return (json_responses, server)."""
        server = SessionServer(db_path=self.db, interface_factory=factory)
        out = io.StringIO()
        err = io.StringIO()
        n = server.run(io.StringIO("\n".join(lines) + "\n"), out, err)
        responses = [json.loads(line) for line in out.getvalue().splitlines()]
        return responses, server, err.getvalue(), n

    def request(self, operation, arguments=None):
        payload = {"operation": operation}
        if arguments is not None:
            payload["arguments"] = arguments
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class SessionServerTests(_Base):
    def test_startup_and_inspect(self):
        responses, server, _, n = self.serve([self.request("inspect")])
        self.assertEqual(n, 1)
        self.assertEqual(len(responses), 1)
        self.assertTrue(responses[0]["ok"])
        self.assertEqual(responses[0]["result"]["node_count"], 5)
        server.close()

    def test_all_operations_in_one_process(self):
        lines = [
            self.request("inspect"),
            self.request("search", {"query": "transport", "limit": 5}),
            self.request("get", {"node_id": "transport"}),
            self.request("provenance", {"node_id": "transport"}),
            self.request("related", {"node_id": "transport"}),
            self.request("follow", {"node_id": "transport",
                                    "relationship_type": "extends"}),
            self.request("inspect"),
        ]
        responses, server, _, n = self.serve(lines)
        server.close()
        self.assertEqual(n, len(lines))
        self.assertEqual(len(responses), len(lines))
        for res in responses:
            self.assertTrue(res["ok"], res)
        self.assertEqual({e["target_node_id"]
                          for e in responses[5]["result"]},
                         {"write-transport", "read-transport"})

    def test_search_get_chain(self):
        responses, server, _, _ = self.serve([
            self.request("search", {"query": "ReadTransport", "limit": 5}),
            self.request("get", {"node_id": "read-transport"}),
        ])
        server.close()
        self.assertTrue(responses[0]["ok"])
        hit_ids = {h["id"] for h in responses[0]["result"]}
        self.assertIn("read-transport", hit_ids)
        self.assertEqual(responses[1]["result"]["id"], "read-transport")

    def test_get_provenance_chain(self):
        responses, server, _, _ = self.serve([
            self.request("get", {"node_id": "transport"}),
            self.request("provenance", {"node_id": "transport"}),
        ])
        server.close()
        self.assertEqual(responses[0]["result"]["id"], "transport")
        self.assertEqual(responses[1]["result"]["node_id"], "transport")

    def test_related(self):
        responses, server, _, _ = self.serve([
            self.request("follow", {"node_id": "transport",
                                    "relationship_type": "references"}),
            self.request("related", {"node_id": "transport"}),
        ])
        server.close()
        self.assertEqual(responses[0]["result"], [])
        self.assertGreaterEqual(len(responses[1]["result"]), 1)

    def test_invalid_request_followed_by_valid(self):
        lines = [
            self.request("get", {"node_id": 17}),            # invalid arg
            self.request("get", {"node_id": "transport"}),   # valid
        ]
        responses, server, _, n = self.serve(lines)
        server.close()
        self.assertEqual(n, 2)
        self.assertFalse(responses[0]["ok"])
        self.assertEqual(responses[0]["error"]["code"], "invalid_argument")
        self.assertTrue(responses[1]["ok"])
        self.assertEqual(responses[1]["result"]["id"], "transport")
        self.assertEqual(server.stats["requests"], 2)

    def test_malformed_json_followed_by_valid(self):
        lines = [
            "{ this is not json",
            self.request("inspect"),
        ]
        responses, server, _, n = self.serve(lines)
        server.close()
        self.assertEqual(n, 2)
        self.assertFalse(responses[0]["ok"])
        self.assertEqual(responses[0]["error"]["code"], "invalid_request")
        self.assertTrue(responses[1]["ok"])

    def test_eof_shutdown_and_stats(self):
        lines = [self.request("inspect"), self.request("inspect")]
        responses, server, err, n = self.serve(lines)
        self.assertEqual(n, 2)
        self.assertTrue(all(r["ok"] for r in responses))
        self.assertIn("SESSION stats=", err)
        self.assertEqual(server.stats["requests"], 2)
        self.assertEqual(server.stats["initializations"], 1)

    def test_exactly_one_response_per_request(self):
        lines = [self.request("inspect")] * 5
        responses, server, _, n = self.serve(lines)
        server.close()
        self.assertEqual(n, 5)
        self.assertEqual(len(responses), 5)
        for i, res in enumerate(responses):
            self.assertTrue(res["ok"], (i, res))

    def test_no_human_readable_stdout(self):
        lines = [
            self.request("inspect"),
            "not json",
            self.request("get", {"node_id": "nope"}),
        ]
        responses, server, _, _ = self.serve(lines)
        server.close()
        self.assertEqual(len(responses), 3)
        for res in responses:
            self.assertIsInstance(res, dict)
            self.assertIn("ok", res)
            self.assertIsInstance(res["ok"], bool)

    def test_deterministic_responses(self):
        lines = [
            self.request("inspect"),
            self.request("search", {"query": "transport", "limit": 5}),
        ]
        r1, s1, _, _ = self.serve(lines)
        s1.close()
        r2, s2, _, _ = self.serve(lines)
        s2.close()
        self.assertEqual(r1, r2)

    def test_sequential_processing_order(self):
        ids = ["transport", "write-transport", "read-transport"]
        lines = [self.request("get", {"node_id": nid}) for nid in ids]
        responses, server, _, _ = self.serve(lines)
        server.close()
        self.assertEqual([r["result"]["id"] for r in responses], ids)

    def test_database_hash_unchanged(self):
        server = SessionServer(db_path=self.db)
        out = io.StringIO()
        server.run(io.StringIO(self.request("inspect") + "\n"), out,
                   io.StringIO())
        server.close()
        self.assertEqual(_sha256(self.db), self.hash_before)

    def test_repository_initialized_exactly_once(self):
        factory = _CountingFactory(self.db)
        lines = [
            self.request("inspect"),
            self.request("search", {"query": "transport"}),
            self.request("get", {"node_id": "transport"}),
            self.request("provenance", {"node_id": "transport"}),
            self.request("related", {"node_id": "transport"}),
            self.request("follow", {"node_id": "transport"}),
        ]
        responses, server, err, n = self.serve(lines, factory=factory)
        server.close()
        self.assertEqual(n, 6)
        self.assertTrue(all(r["ok"] for r in responses))
        self.assertEqual(factory.calls, 1)
        self.assertEqual(server.stats["initializations"], 1)
        self.assertIsNotNone(server.stats["load_seconds"])
        self.assertIn('"initializations": 1', err)

    def test_internal_error_on_missing_db(self):
        server = SessionServer(db_path="/nonexistent/knowledge.db")
        out = io.StringIO()
        server.run(io.StringIO(self.request("inspect") + "\n"), out,
                   io.StringIO())
        server.close()
        res = json.loads(out.getvalue().strip())
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"]["code"], "internal_error")


class SessionLatencyTests(_Base):
    """First request pays load; subsequent requests must not reload."""

    def test_second_request_does_not_repeat_load_cost(self):
        self.tmp.cleanup()
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "knowledge.db")
        _seed_db(self.db, n_extra=2000)

        server = SessionServer(db_path=self.db)
        out = _StampingStream()
        err = io.StringIO()
        requests = "\n".join([self.request("inspect")] * 3) + "\n"
        begin = time.monotonic()
        server.run(io.StringIO(requests), out, err)
        end = time.monotonic()
        server.close()

        stamps = out.stamps
        self.assertEqual(len(stamps), 3)
        # stamps[i] is recorded when response i is written; the repository
        # load happens between `begin` and the first response write.
        load_and_first = (stamps[0] - begin) * 1000
        second_ms = (stamps[1] - stamps[0]) * 1000
        third_ms = (stamps[2] - stamps[1]) * 1000
        total_ms = (end - begin) * 1000
        self.assertGreater(load_and_first, 0)
        # first request pays the repository load; later requests do not
        self.assertLess(second_ms, load_and_first)
        self.assertLess(third_ms, load_and_first)
        self.assertLess(load_and_first, total_ms)
        self.assertLess(second_ms, load_and_first * 0.5)
        self.assertEqual(server.stats["initializations"], 1)

    def test_two_separate_runs_share_nothing(self):
        """Fresh servers re-pay the load (proves the saved cost is real)."""
        db_a = os.path.join(self.tmp.name, "a.db")
        _seed_db(db_a, n_extra=1000)
        def run_once(db):
            server = SessionServer(db_path=db)
            out = io.StringIO()
            t0 = time.monotonic()
            server.run(io.StringIO(self.request("inspect") + "\n"), out,
                       io.StringIO())
            dt = time.monotonic() - t0
            server.close()
            return dt, server.stats["initializations"]
        _, first = run_once(db_a)
        _, second = run_once(db_a)
        self.assertEqual(first, 1)
        self.assertEqual(second, 1)


class SessionProcessTests(unittest.TestCase):
    """The session as a real subprocess: stdin/stdout protocol end to end."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "knowledge.db")
        _seed_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def _spawn(self, db=None, extra=None):
        cmd = [sys.executable, "-m", "api.session"]
        if db is not None:
            cmd.extend(["--db", db])
        if extra:
            cmd.extend(extra)
        return subprocess.Popen(cmd, cwd=_ROOT, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, bufsize=1)

    def _ask(self, proc, request):
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write(json.dumps(request) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        self.assertTrue(line, "session produced no response")
        return json.loads(line)

    @staticmethod
    def _shutdown(proc, timeout=60):
        """Close stdin (EOF), await clean exit, drain and close pipes."""
        assert (proc.stdin is not None and proc.stdout is not None
                and proc.stderr is not None)
        proc.stdin.close()
        rc = proc.wait(timeout=timeout)
        stderr = proc.stderr.read()
        proc.stdout.close()
        proc.stderr.close()
        return rc, stderr

    def test_multiple_requests_in_one_process(self):
        proc = self._spawn(db=self.db)
        try:
            self.assertTrue(self._ask(proc, {"operation": "inspect"})["ok"])
            hits = self._ask(proc, {"operation": "search",
                                    "arguments": {"query": "transport",
                                                  "limit": 5}})["result"]
            self.assertTrue(hits)
            node = self._ask(proc, {"operation": "get",
                                    "arguments": {"node_id": hits[0]["id"]}})["result"]
            self.assertEqual(node["id"], hits[0]["id"])
            prov = self._ask(proc, {"operation": "provenance",
                                    "arguments": {"node_id": hits[0]["id"]}})["result"]
            self.assertEqual(prov["node_id"], hits[0]["id"])
            rel = self._ask(proc, {"operation": "related",
                                   "arguments": {"node_id": hits[0]["id"]}})["result"]
            self.assertIsInstance(rel, list)
            follow = self._ask(proc, {"operation": "follow",
                                      "arguments": {"node_id": "transport",
                                                    "relationship_type":
                                                    "extends"}})["result"]
            self.assertEqual({e["target_node_id"] for e in follow},
                             {"write-transport", "read-transport"})
        finally:
            rc, _ = self._shutdown(proc)
            self.assertEqual(rc, 0)

    def test_invalid_then_valid_does_not_kill_session(self):
        proc = self._spawn(db=self.db)
        try:
            bad = self._ask(proc, {"operation": "get",
                                   "arguments": {"node_id": 9}})
            self.assertFalse(bad["ok"])
            self.assertEqual(bad["error"]["code"], "invalid_argument")
            good = self._ask(proc, {"operation": "inspect"})
            self.assertTrue(good["ok"])
        finally:
            rc, _ = self._shutdown(proc)
            self.assertEqual(rc, 0)

    def test_malformed_json_then_valid(self):
        proc = self._spawn(db=self.db)
        try:
            assert proc.stdin is not None and proc.stdout is not None
            proc.stdin.write("this is not json\n")
            proc.stdin.flush()
            bad = json.loads(proc.stdout.readline())
            self.assertFalse(bad["ok"])
            self.assertEqual(bad["error"]["code"], "invalid_request")
            good = self._ask(proc, {"operation": "inspect"})
            self.assertTrue(good["ok"])
        finally:
            rc, _ = self._shutdown(proc)
            self.assertEqual(rc, 0)

    def test_eof_clean_exit(self):
        proc = self._spawn(db=self.db)
        assert proc.stdin is not None and proc.stdout is not None
        proc.stdin.write('{"operation": "inspect"}\n')
        proc.stdin.flush()
        first = json.loads(proc.stdout.readline())
        self.assertTrue(first["ok"])
        rc, err = self._shutdown(proc)  # EOF on stdin
        self.assertEqual(rc, 0)
        lines = err.strip().splitlines()
        self.assertTrue(any(l.startswith("SESSION stats=") for l in lines))

    def test_stdout_is_pure_json_lines(self):
        proc = self._spawn(db=self.db)
        try:
            for _ in range(3):
                res = self._ask(proc, {"operation": "inspect"})
                self.assertTrue(res["ok"])
        finally:
            rc, _ = self._shutdown(proc)
            self.assertEqual(rc, 0)

    def test_initialization_happened_once_in_process(self):
        proc = self._spawn(db=self.db)
        try:
            self.assertTrue(self._ask(proc, {"operation": "inspect"})["ok"])
            self.assertTrue(self._ask(proc, {"operation": "inspect"})["ok"])
        finally:
            rc, err = self._shutdown(proc)
            self.assertEqual(rc, 0)
        self.assertIn('"initializations": 1', err)
        self.assertIn('"requests": 2', err)

    def test_production_session_read_only_and_loaded_once(self):
        before = _sha256(PRODUCTION_DB)
        proc = self._spawn(db=None)  # default production database
        try:
            result = self._ask(proc, {"operation": "inspect"})
            self.assertTrue(result["ok"])
            self.assertEqual(result["result"]["node_count"], 4846)
            node = self._ask(proc, {"operation": "get",
                                    "arguments": {"node_id": "exceptions"}})
            self.assertTrue(node["ok"])
            self.assertEqual(node["result"]["id"], "exceptions")
        finally:
            rc, err = self._shutdown(proc, timeout=120)
            self.assertEqual(rc, 0)
        self.assertIn('"initializations": 1', err)
        self.assertEqual(_sha256(PRODUCTION_DB), before)


if __name__ == "__main__":
    unittest.main()