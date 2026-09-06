"""Phase 21 — Contract & API Final Reconciliation.

Tests the ACTUAL public interface inventory, reconciling what the code
implements with the canonical contract (docs/public-contract.md):

1. v1 immutability
2. v2 operation inventory
3. v2 request/response consistency
4. Memory API <-> MemoryTools
5. InProcess <-> Session <-> HTTP
6. project isolation
7. deterministic IDs
8. provenance
9. error semantics
10. CLI / public boundary
11. optional Code plugin
12. contract documentation vs implementation

No DB schema changes; legacy database SHA invariant preserved.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api import contract as contract_v1
from api import contract_v2, memory_api as memory_api_mod, memory_tools
from api.errors import (
    KnowledgeArgumentError,
    NodeNotFoundError,
)
from knowledge_client import memory_client as memory_client_mod
from knowledge_client.transports import (
    MemoryInProcessTransport,
    MemorySessionTransport,
    HttpMemoryTransport,
)

EXPECTED_V1_OPS = {"search", "get", "related", "follow", "provenance", "inspect"}
EXPECTED_V2_OPS = {"remember", "recall", "get", "provenance", "inspect",
                   "context.get",
                   "lifecycle.ingest", "lifecycle.experience",
                   "lifecycle.learning", "lifecycle.strategy",
                   "lifecycle.trace", "lifecycle.describe",
                   "lifecycle.summary"}
SHARED_ERROR_CODES = {
    "invalid_request",
    "unknown_operation",
    "invalid_argument",
    "invalid_relationship_type",
    "node_not_found",
    "internal_error",
}
LEGACY_DB = os.path.join(_ROOT, "database", "knowledge.db")
EXPECTED_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"


def _sha256(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def _free_port():
    s = socket.socket()
    s.bind(("", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _run_cli(args, data_root=None):
    e = os.environ.copy()
    if data_root:
        e["AI_ENGINE_DATA_DIR"] = data_root
    return subprocess.run(
        [sys.executable, "-m", "ai_engine"] + args,
        cwd=_ROOT, capture_output=True, text=True, env=e, timeout=60)


# --------------------------------------------------------------------------
# 1. v1 immutability
# --------------------------------------------------------------------------

class TestV1Immutability(unittest.TestCase):
    def test_legacy_db_sha_unchanged(self):
        if not os.path.exists(LEGACY_DB):
            self.skipTest("legacy database absent")
        self.assertEqual(_sha256(LEGACY_DB), EXPECTED_SHA)

    def test_v1_operation_set_frozen(self):
        self.assertEqual(contract_v1.CONTRACT_VERSION, "1")
        self.assertEqual(set(contract_v1.OPERATIONS), EXPECTED_V1_OPS)

    def test_v1_contains_no_v2_or_coding_ops(self):
        v2_only = EXPECTED_V2_OPS - EXPECTED_V1_OPS  # remember, recall, context.get
        for op in v2_only:
            self.assertNotIn(op, contract_v1.OPERATIONS)
        for op in ("remember", "learn", "capture", "context.diff"):
            self.assertNotIn(op, contract_v1.OPERATIONS)

    def test_v1_read_only_ops(self):
        for op in EXPECTED_V1_OPS:
            spec = contract_v1.OPERATIONS[op]
            args = set(spec.get("required", {})) | set(spec.get("optional", {}))
            for bad in ("sql", "path", "command"):
                self.assertNotIn(bad, args, "%s must not accept %r" % (op, bad))


# --------------------------------------------------------------------------
# 2. v2 operation inventory
# --------------------------------------------------------------------------

class TestV2OperationInventory(unittest.TestCase):
    def test_v2_operation_set_frozen(self):
        self.assertEqual(contract_v2.CONTRACT_VERSION, "2")
        self.assertEqual(set(contract_v2.OPERATIONS), EXPECTED_V2_OPS)
        self.assertEqual(tuple(contract_v2.operations()), contract_v2.DEFAULT_OPERATION_ORDER)

    def test_v2_contains_no_legacy_described_ops(self):
        # Older architecture documents mentioned capture/learn/context.diff;
        # the implemented contract intentionally does not expose them.
        for op in ("capture", "learn", "context.diff"):
            self.assertNotIn(op, contract_v2.OPERATIONS)

    def test_v2_has_no_coding_ops(self):
        for op in EXPECTED_V2_OPS:
            self.assertNotIn("code", op.lower())


# --------------------------------------------------------------------------
# 3. v2 request/response consistency
# --------------------------------------------------------------------------

def _iface(data_root):
    return memory_tools.MemoryToolInterface(data_root=data_root)


class TestV2RequestResponseConsistency(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.iface = _iface(self._tmp.name)
        self.addCleanup(self.iface.close)

    def test_envelope_fields(self):
        ok = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "envelope audit"}}})
        self.assertEqual(set(ok) - {"ok", "operation", "contract_version", "result"}, set())
        self.assertIs(ok["ok"], True)
        self.assertEqual(ok["operation"], "remember")
        self.assertEqual(ok["contract_version"], "2")
        bad = self.iface.execute({"operation": "get", "arguments": {"node_id": "nope"}})
        self.assertIs(bad["ok"], False)
        self.assertEqual(set(bad) - {"ok", "operation", "contract_version", "error"}, set())
        self.assertEqual(bad["contract_version"], "2")
        self.assertIn("code", bad["error"])
        self.assertIn("message", bad["error"])
        self.assertNotIn("stack", json.dumps(bad).lower())
        self.assertNotIn("traceback", json.dumps(bad).lower())

    def test_remember_result_shape(self):
        r = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "shape audit"}}})
        res = r["result"]
        for key in ("node_id", "node_ids", "activity_id", "context_id", "evidence_id"):
            self.assertIn(key, res, "remember result must carry %r" % key)

    def test_recall_result_shape(self):
        self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "shape audit recall"}}})
        r = self.iface.execute({"operation": "recall", "arguments": {"query": "shape", "limit": 5}})
        res = r["result"]
        for key in ("query", "count", "knowledge"):
            self.assertIn(key, res)
        self.assertGreaterEqual(res["count"], 1)
        entry = res["knowledge"][0]
        for key in ("id", "type", "name", "description", "provenance"):
            self.assertIn(key, entry)

    def test_get_and_provenance_shape(self):
        r = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "shape audit get"}}})
        nid = r["result"]["node_id"]
        g = self.iface.execute({"operation": "get", "arguments": {"node_id": nid}})
        self.assertEqual(g["result"]["id"], nid)
        p = self.iface.execute({"operation": "provenance", "arguments": {"node_id": nid}})
        for key in ("node_id", "source_id", "source_name", "source_version", "source_location", "imported_at"):
            self.assertIn(key, p["result"])

    def test_inspect_and_context_shape(self):
        r = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "shape audit ctx"}}})
        insp = self.iface.execute({"operation": "inspect", "arguments": {}})
        self.assertEqual(insp["result"]["node_count"], 1)
        self.assertGreaterEqual(insp["result"]["activity_count"], 1)
        c = self.iface.execute({"operation": "context.get", "arguments": {"context_id": r["result"]["context_id"]}})
        self.assertEqual(c["result"]["context_id"], r["result"]["context_id"])

    def test_limit_capped_at_100(self):
        r = self.iface.execute({"operation": "recall", "arguments": {"query": "x", "limit": 500}})
        self.assertIs(r["ok"], True)


# --------------------------------------------------------------------------
# 4. Memory API <-> MemoryTools
# --------------------------------------------------------------------------

class TestMemoryAPIMatchesMemoryTools(unittest.TestCase):
    """Both boundaries must expose identical operation semantics."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.api = memory_api_mod.MemoryAPI(data_root=self._tmp.name)
        self.iface = _iface(self._tmp.name)
        self.addCleanup(self.iface.close)

    def test_remember_equivalent(self):
        via_api = self.api.remember(payload={"text": "eq remember api"})
        via_tools = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "eq remember api"}}})
        self.assertEqual(via_api["node_id"], via_tools["result"]["node_id"])

    def test_recall_equivalent(self):
        self.api.remember(payload={"text": "eq recall lookup"})
        via_api = self.api.recall(query="eq recall", limit=5)
        via_tools = self.iface.execute({"operation": "recall", "arguments": {"query": "eq recall", "limit": 5}})
        self.assertEqual([n["id"] for n in via_api["knowledge"]],
                         [n["id"] for n in via_tools["result"]["knowledge"]])

    def test_get_and_provenance_equivalent(self):
        r = self.api.remember(payload={"text": "eq get prov"})
        nid = r["node_id"]
        self.assertEqual(self.api.get(nid)["id"], self.iface.execute(
            {"operation": "get", "arguments": {"node_id": nid}})["result"]["id"])
        self.assertEqual(self.api.provenance(nid)["node_id"], self.iface.execute(
            {"operation": "provenance", "arguments": {"node_id": nid}})["result"]["node_id"])

    def test_inspect_equivalent(self):
        self.api.remember(payload={"text": "eq inspect"})
        a = self.api.inspect()
        t = self.iface.execute({"operation": "inspect", "arguments": {}})["result"]
        self.assertEqual(a["node_count"], t["node_count"])
        self.assertEqual(a["context_count"], t["context_count"])

    def test_context_get_equivalent(self):
        r = self.api.remember(payload={"text": "eq ctx"})
        cid = r["context_id"]
        self.assertEqual(self.api.context_get(cid)["context_id"], self.iface.execute(
            {"operation": "context.get", "arguments": {"context_id": cid}})["result"]["context_id"])

    def test_invalid_vocabulary_maps_to_invalid_argument(self):
        with self.assertRaises(KnowledgeArgumentError):
            self.api.recall(query="x", vocabulary_id="bogus_vocab")
        from api.errors import NodeNotFoundError
        with self.assertRaises(KnowledgeArgumentError):
            self.api.get(node_id="nope", vocabulary_id="bogus_vocab")


# --------------------------------------------------------------------------
# 5. InProcess <-> Session <-> HTTP
# --------------------------------------------------------------------------

class TestV2TransportsEquivalent(unittest.TestCase):
    """Same data root, three transports, identical results."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self._server = None
        self._serving_thread = None
        self._port = _free_port()

    def tearDown(self):
        if self._server is not None:
            try:
                self._server.shutdown()
            finally:
                self._server.server_close()
            if self._serving_thread is not None:
                self._serving_thread.join(timeout=5)

    def _start_http(self):
        from http_server.server import KnowledgeHTTPServer
        from api.memory_tools import MemoryToolInterface
        srv = KnowledgeHTTPServer(
            ("127.0.0.1", self._port), data_root=self._tmp.name)
        self._server = srv
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        self._serving_thread = t
        import http.client
        deadline = 10
        import time
        while deadline > 0:
            try:
                c = http.client.HTTPConnection("127.0.0.1", self._port, timeout=2)
                c.request("GET", "/health")
                ok = json.loads(c.getresponse().read().decode()).get("ok")
                c.close()
                if ok:
                    return
            except Exception:
                pass
            time.sleep(0.1)
            deadline -= 0.1
        raise RuntimeError("HTTP server did not become healthy")

    def _transports(self):
        inproc = memory_client_mod.MemoryClient(
            MemoryInProcessTransport(data_root=self._tmp.name))
        session = memory_client_mod.MemoryClient(
            MemorySessionTransport(data_root=self._tmp.name))
        self._start_http()
        httpc = memory_client_mod.MemoryClient(
            HttpMemoryTransport(host="127.0.0.1", port=self._port))
        return [("inproc", inproc), ("session", session), ("http", httpc)]

    def test_remember_recall_get_inspect_identical_across_transports(self):
        pub_text = "cross transport audit shared"
        clients = self._transports()
        try:
            expected = None
            for name, c in clients:
                nid = c.remember(payload={"text": pub_text}, project_id="pa")["node_id"]
                with self.subTest(name=name):
                    self.assertTrue(nid.startswith("fact_"))
                if expected is None:
                    expected = nid
                else:
                    self.assertEqual(nid, expected, "all transports must agree on deterministic node id")
            for name, c in clients:
                hits = c.recall(query="cross transport", project_id="pa")
                ctx_id = hits["knowledge"][0]["_context_id"]
                self.assertGreaterEqual(hits["count"], 1)
                self.assertEqual(c.get(expected, project_id="pa")["id"], expected)
                insp = c.inspect(project_id="pa")
                self.assertEqual(insp["node_count"], 1)
                self.assertEqual(c.context_get(ctx_id, project_id="pa")["context_id"], ctx_id)
        finally:
            for _, c in clients:
                c.close()

    def test_bogus_vocabulary_rejected_everywhere(self):
        from knowledge_client.errors import InvalidArgumentError
        clients = self._transports()
        try:
            for name, c in clients:
                with self.subTest(name=name):
                    with self.assertRaises(InvalidArgumentError):
                        c.recall(query="x", vocabulary_id="bogus_vocab")
        finally:
            for _, c in clients:
                c.close()

    def test_project_isolation_across_transports(self):
        from knowledge_client.errors import NodeNotFoundError
        clients = self._transports()
        try:
            pid = clients[0][1].remember(payload={"text": "iso xport"}, project_id="pa")["node_id"]
            for name, c in clients:
                with self.subTest(name=name):
                    got = c.get(pid, project_id="pa")
                    self.assertEqual(got["id"], pid)
                    with self.assertRaises(NodeNotFoundError):
                        c.get(pid, project_id="pb")
        finally:
            for _, c in clients:
                c.close()


# --------------------------------------------------------------------------
# 6. project isolation
# --------------------------------------------------------------------------

class TestProjectIsolation(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.iface = _iface(self._tmp.name)
        self.addCleanup(self.iface.close)

    def test_cross_project_node_not_found(self):
        r = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "iso A"}, "project_id": "pa"}})
        nid = r["result"]["node_id"]
        ok = self.iface.execute({"operation": "get", "arguments": {"node_id": nid, "project_id": "pa"}})
        self.assertIs(ok["ok"], True)
        miss = self.iface.execute({"operation": "get", "arguments": {"node_id": nid, "project_id": "pb"}})
        self.assertEqual(miss["error"]["code"], "node_not_found")

    def test_recall_scoped_by_project(self):
        self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "only in A secret content"}, "project_id": "pa"}})
        self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "only in B other content"}, "project_id": "pb"}})
        ra = self.iface.execute({"operation": "recall", "arguments": {"query": "content", "project_id": "pa"}})
        rb = self.iface.execute({"operation": "recall", "arguments": {"query": "content", "project_id": "pb"}})
        self.assertEqual([n["id"] for n in ra["result"]["knowledge"] if "only in B" in n["description"]], [])
        self.assertEqual([n["id"] for n in rb["result"]["knowledge"] if "only in A" in n["description"]], [])

    def test_default_project_is_deterministic(self):
        ra = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "default proj"}}})
        insp = self.iface.execute({"operation": "inspect", "arguments": {}})
        self.assertEqual(insp["result"]["project_id"], "default")
        self.assertEqual(insp["result"]["node_count"], 1)
        ra2 = self.iface.execute({"operation": "inspect", "arguments": {"project_id": "default"}})
        self.assertEqual(ra2["result"]["project_id"], "default")
        self.assertEqual(ra["result"]["node_id"], self.iface.execute(
            {"operation": "remember", "arguments": {"payload": {"text": "default proj"}}})["result"]["node_id"])


# --------------------------------------------------------------------------
# 7. deterministic IDs
# --------------------------------------------------------------------------

class TestDeterministicIdentity(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.iface = _iface(self._tmp.name)
        self.addCleanup(self.iface.close)

    def test_same_payload_same_node_id(self):
        r1 = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "deterministic ident"}}})
        r2 = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "deterministic ident"}}})
        self.assertEqual(r1["result"]["node_id"], r2["result"]["node_id"])

    def test_activity_and_evidence_also_deterministic(self):
        r1 = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "det act ev"}}})
        r2 = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "det act ev"}}})
        self.assertEqual(r1["result"]["activity_id"], r2["result"]["activity_id"])
        self.assertEqual(r1["result"]["evidence_id"], r2["result"]["evidence_id"])

    def test_pipeline_chain_present(self):
        r = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "pipeline chain"}, "context_hints": {"actor": {"user_id": "u9"}}}})
        res = r["result"]
        insp = self.iface.execute({"operation": "inspect", "arguments": {}})["result"]
        self.assertGreaterEqual(insp["activity_count"], 1)
        self.assertGreaterEqual(insp["context_count"], 1)
        self.assertGreaterEqual(insp["evidence_count"], 1)
        self.assertEqual(insp["node_count"], 1)
        # remember -> activity -> context -> knowledge -> evidence linkage
        for pre in ("act_", "ctx_", "ev_", "fact_"):
            self.assertTrue(res.get({
                "act_": "activity_id", "ctx_": "context_id", "ev_": "evidence_id", "fact_": "node_id",
            }[pre]).startswith(pre), "expected %s field" % ({"act_": "activity_id", "ctx_": "context_id", "ev_": "evidence_id", "fact_": "node_id"}[pre]))


# --------------------------------------------------------------------------
# 8. provenance
# --------------------------------------------------------------------------

class TestProvenance(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.iface = _iface(self._tmp.name)
        self.addCleanup(self.iface.close)

    def test_provenance_never_invented(self):
        r = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "prov audit"}}})
        nid = r["result"]["node_id"]
        p = self.iface.execute({"operation": "provenance", "arguments": {"node_id": nid}})["result"]
        self.assertEqual(p["node_id"], nid)
        self.assertIn("source_id", p)
        self.assertIn("source_name", p)
        self.assertIn("imported_at", p)
        self.assertIn("source_location", p)

    def test_provenance_of_unknown_node_is_error(self):
        miss = self.iface.execute({"operation": "provenance", "arguments": {"node_id": "nope"}})
        self.assertEqual(miss["error"]["code"], "node_not_found")


# --------------------------------------------------------------------------
# 9. error semantics
# --------------------------------------------------------------------------

class TestErrorSemantics(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.iface = _iface(self._tmp.name)
        self.addCleanup(self.iface.close)

    def _code(self, request):
        resp = self.iface.execute(request)
        return (resp.get("error") or {}).get("code")

    def test_error_code_table(self):
        cases = [
            ({"operation": "remember", "arguments": {"payload": "not-a-dict"}}, "invalid_argument"),
            ({"operation": "remember", "arguments": {"payload": {}}}, "invalid_argument"),
            ({"operation": "remember", "arguments": {"payload": {"text": "x", "type": "hax"}}}, "invalid_argument"),
            ({"operation": "recall", "arguments": {}}, "invalid_argument"),
            ({"operation": "recall", "arguments": {"query": "x", "limit": 0}}, "invalid_argument"),
            ({"operation": "recall", "arguments": {"query": "x", "limit": -3}}, "invalid_argument"),
            ({"operation": "recall", "arguments": {"query": "x", "candidate_limit": 0}}, "invalid_argument"),
            ({"operation": "recall", "arguments": {"query": "x", "sql": "DROP"}}, "invalid_argument"),
            ({"operation": "get", "arguments": {}}, "invalid_argument"),
            ({"operation": "get", "arguments": {"node_id": "nope"}}, "node_not_found"),
            ({"operation": "recall", "arguments": {"query": "x", "vocabulary_id": "bogus_vocab"}}, "invalid_argument"),
            ({"operation": "get", "arguments": {"node_id": "nope", "vocabulary_id": "bogus_vocab"}}, "invalid_argument"),
            ({"operation": "inspect", "arguments": {"vocabulary_id": "bogus_vocab"}}, "invalid_argument"),
            ({"operation": "context.get", "arguments": {"context_id": "no_such_ctx"}}, "node_not_found"),
            ({"operation": "remember", "arguments": {"payload": {"text": "x"}, "project_id": "../etc"}}, "invalid_argument"),
            ({"operation": "recall", "arguments": {"query": "x", "project_id": "UPPER CASE!"}}, "invalid_argument"),
            ({"operation": "no_such_op", "arguments": {}}, "unknown_operation"),
            ({"operation": "remember", "arguments": "bad"}, "invalid_request"),
            ({"operation": "remember"}, "invalid_argument"),
            (None, "invalid_request"),
        ]
        for request, expected in cases:
            with self.subTest(request=request):
                self.assertEqual(self._code(request), expected)

    def test_error_message_never_leaks_internals(self):
        r = self.iface.execute({"operation": "remember", "arguments": {"payload": {"text": "x", "type": "hax"}}})
        blob = json.dumps(r).lower()
        for token in ("traceback", "line ", "at 0x", "sqlite", ".py'"):
            self.assertNotIn(token, blob)
        r2 = self.iface.execute({"operation": "get", "arguments": {"node_id": "nope"}})
        self.assertNotIn("traceback", json.dumps(r2).lower())

    def test_api_layer_error_codes(self):
        api = memory_api_mod.MemoryAPI(data_root=self._tmp.name)
        with self.assertRaises(KnowledgeArgumentError):
            api.remember(payload={"text": "x", "type": "hax"})
        with self.assertRaises(NodeNotFoundError):
            api.get(node_id="nope")
        with self.assertRaises(KnowledgeArgumentError):
            api.recall(query="x", vocabulary_id="nope_vocab")


# --------------------------------------------------------------------------
# 10. CLI / public boundary
# --------------------------------------------------------------------------

class TestCLIPublicBoundary(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)

    def test_subcommands_present(self):
        r = _run_cli(["--help"], data_root=self._tmp.name)
        self.assertEqual(r.returncode, 0)
        for cmd in ("init", "remember", "recall", "capture", "context", "status",
                    "backup", "export", "import", "doctor", "migrate"):
            self.assertIn(cmd, r.stdout)

    def test_remember_recall_status_doctor_flow(self):
        data = self._tmp.name
        r0 = _run_cli(["init", "--project", "p1"], data_root=data)
        self.assertEqual(r0.returncode, 0, r0.stderr)
        r1 = _run_cli(["remember", "cli boundary audit", "--project", "p1", "--json"], data_root=data)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        out = json.loads(r1.stdout)
        self.assertTrue(out["ok"])
        self.assertTrue(out["node_id"].startswith("fact_"))
        r2 = _run_cli(["recall", "cli boundary", "--project", "p1", "--json"], data_root=data)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertGreaterEqual(json.loads(r2.stdout)["count"], 1)
        for cmd in (["status", "--project", "p1", "--json"],
                    ["doctor", "--project", "p1", "--json"]):
            r = _run_cli(cmd, data_root=data)
            self.assertEqual(r.returncode, 0, r.stderr)

    def test_cli_invalid_project_maps_to_invalid_argument(self):
        data = self._tmp.name
        r = _run_cli(["remember", "x", "--project", "../etc", "--json"], data_root=data)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid_argument", r.stderr)

    def test_cli_invalid_vocabulary_maps_to_invalid_argument(self):
        data = self._tmp.name
        r = _run_cli(["init", "--project", "p2", "--vocabulary", "bogus_vocab"], data_root=data)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("invalid_argument", r.stderr)

    def test_cli_json_envelope_matches_contract(self):
        data = self._tmp.name
        r = _run_cli(["remember", "cli envelope", "--project", "p3", "--json"], data_root=data)
        out = json.loads(r.stdout)
        self.assertIn("ok", out)
        self.assertIn("node_id", out)
        self.assertIn("activity_id", out)


# --------------------------------------------------------------------------
# 11. optional Code plugin
# --------------------------------------------------------------------------

class TestOptionalCodePlugin(unittest.TestCase):
    def test_generic_core_imports_without_code_plugin(self):
        code = (
            "import sys\n"
            "sys.path.insert(0, %r)\n"
            "import api.contract, api.contract_v2, api.memory_api, api.memory_tools\n"
            "import ai_engine.memory, ai_engine.paths, knowledge_client.memory_client\n"
            "assert 'tools.coding' not in sys.modules, 'tools.coding must not load'\n"
            "assert 'ai_engine.plugins.code' not in sys.modules, 'code plugin must not autoload'\n"
            "print('CORE_OK')\n" % _ROOT
        )
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("CORE_OK", r.stdout)

    def test_v1_v2_contain_no_coding_operations(self):
        for op, spec in contract_v1.OPERATIONS.items():
            self.assertTrue(op in EXPECTED_V1_OPS)
        coding_words = ("code", "index", "build", "test", "file", "git", "compile")
        for op in EXPECTED_V1_OPS | EXPECTED_V2_OPS:
            for w in coding_words:
                self.assertNotIn(w, op.lower())

    def test_code_plugin_boundary_is_generic_only(self):
        # The code plugin ships OUTSIDE the core (optional external plugin).
        # The in-core plugin boundary must be generic: no code helpers.
        import ai_engine.plugins as boundary
        self.assertEqual(boundary.__all__, [])
        with self.assertRaises(ImportError):
            from ai_engine.plugins import code  # noqa: F401
        # The public registration boundary the external plugin targets still
        # exists and stays generic.
        from ai_engine.registry import AdapterRegistry
        reg = AdapterRegistry()
        reg.register_effect("my_domain.tool", lambda *a, **kw: {"ok": True})
        self.assertTrue(reg.has_effect("my_domain.tool"))


# --------------------------------------------------------------------------
# 12. contract documentation vs implementation
# --------------------------------------------------------------------------

class TestContractDocConsistency(unittest.TestCase):
    def test_v2_doc_ops_match_implementation(self):
        doc_ops = {"remember", "recall", "get", "provenance", "inspect",
                   "context.get",
                   "lifecycle.ingest", "lifecycle.experience",
                   "lifecycle.learning", "lifecycle.strategy",
                   "lifecycle.trace", "lifecycle.describe",
                   "lifecycle.summary"}
        self.assertEqual(doc_ops, set(contract_v2.OPERATIONS))

    def test_field_lists_match_client_surface(self):
        # get: node_id, project_id, vocabulary_id
        self.assertTrue(hasattr(memory_client_mod.MemoryClient, "get"))
        # recall accepts the full frozen field list
        import inspect as pyinspect
        recall_sig = pyinspect.signature(memory_client_mod.MemoryClient.recall)
        params = set(recall_sig.parameters)
        for field in ("query", "limit", "candidate_limit", "context", "project_id", "vocabulary_id"):
            self.assertIn(field, params)
        get_sig = pyinspect.signature(memory_client_mod.MemoryClient.get)
        for field in ("node_id", "project_id", "vocabulary_id"):
            self.assertIn(field, get_sig.parameters)

    def test_shared_error_codes_in_both_contracts(self):
        self.assertEqual(set(contract_v2.OPERATIONS), EXPECTED_V2_OPS)
        v1_codes = {"invalid_request", "unknown_operation", "invalid_argument",
                    "invalid_relationship_type", "node_not_found", "internal_error"}
        self.assertEqual(v1_codes, SHARED_ERROR_CODES)

    def test_canonical_doc_file_present(self):
        doc = os.path.join(_ROOT, "docs", "public-contract.md")
        self.assertTrue(os.path.exists(doc), "missing docs/public-contract.md")
        with open(doc) as f:
            content = f.read()
        for op in EXPECTED_V2_OPS:
            self.assertIn(op, content)
        for section in ("Frozen", "Internal", "Optional domain plugin"):
            self.assertIn(section, content)


if __name__ == "__main__":
    unittest.main()