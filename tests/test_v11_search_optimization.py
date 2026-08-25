"""v1.1 search-internals optimization regression tests.

Proves the optimization is behavior-preserving:

* search results are byte-identical to the legacy (hydrate-everything)
  algorithm, including dict key order, scores and tie ordering;
* ranking/filtering/limiting happen on lightweight fields and only the final
  returned nodes are hydrated (query counts stay bounded -- N+1 eliminated);
* ``load_from_repository`` builds an in-memory mirror identical to the legacy
  per-node construction while issuing a constant number of queries;
* production database stays read-only; Contract v1 stays frozen.
"""

import hashlib
import json
import os
import sqlite3
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.contract import CONTRACT_VERSION
from api.knowledge_api import KnowledgeAPI
from api.tools import ToolInterface
from retrieval.knowledge import KnowledgeStore
from retrieval.repository import (
    DEFAULT_KNOWLEDGE_DB,
    KnowledgeRepository,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _bytes(obj):
    """Canonical byte serialization (captures key order too)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


class QueryCounter:
    """Count SQL statements executed on a repository connection."""

    def __init__(self, repo):
        self.repo = repo
        self.counts = []

    def __enter__(self):
        self.counts = []
        self.repo.conn.set_trace_callback(self._on_statement)
        return self

    def _on_statement(self, stmt):
        self.counts.append(stmt)

    def __exit__(self, *exc):
        self.repo.conn.set_trace_callback(None)

    @property
    def total(self):
        return len(self.counts)

    @property
    def selects(self):
        return sum(1 for s in self.counts
                   if s.lstrip().upper().startswith("SELECT"))


def legacy_search_nodes(repo, query, limit=None):
    """Verbatim pre-v1.1 repository search (hydrate every match up front)."""
    words = [w for w in query.lower().split() if w]
    rows = repo.conn.execute(
        "SELECT id, type, name, description, source_id, metadata "
        "FROM nodes").fetchall()
    results = []
    for row in rows:
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        text = " ".join(str(v) for v in (
            row["id"], row["type"], row["name"], row["description"], meta
        )).lower()
        score = sum(1 for w in words if w in text)
        if score > 0:
            node = repo._node_dict(row, with_relationships=True)
            node["_score"] = score
            results.append((score, node))
    results.sort(key=lambda x: x[0], reverse=True)
    if limit is not None:
        results = results[:limit]
    return [n for _, n in results]


def legacy_api_search(api, query, node_type=None, limit=None):
    """Verbatim pre-v1.1 KnowledgeAPI.search."""
    results = api.store.repo.search_nodes.__wrapped__(api.store.repo, query) \
        if False else legacy_search_nodes(api.store.repo, query, limit=None)
    if node_type is not None:
        results = [n for n in results if n.get("type") == node_type]
    results.sort(key=lambda n: (-(n.get("_score") or 0), n.get("id") or ""))
    if limit is not None:
        results = results[:limit]
    return results


# ---------------------------------------------------------------------------
# shared fixture
# ---------------------------------------------------------------------------

def build_fixture():
    """Small graph exercising ties, unicode, NULL labels, dangling targets,
    multi-relationship nodes, metadata extras and source-less nodes."""
    repo = KnowledgeRepository(":memory:").initialize()
    sid = repo.add_source("fixture-source", version="1.0",
                          location="fixtures/v11.json")
    # one node deliberately WITHOUT a source row reference
    repo.add_node("mod-alpha", "technology", "Alpha Module",
                  "The alpha module provides alpha things.", source_id=sid,
                  metadata={"doc": "library/alpha.rst"})
    repo.add_node("mod-beta", "technology", "Beta Module",
                  "Beta extends alpha ideas.", source_id=sid,
                  metadata={"évidence": "unicode value", "n": 1})
    repo.add_node("mod-gamma", "technology", "Gamma",
                  "Unrelated gamma content.", source_id=sid)
    repo.add_node("ex-tie-1", "example", ">>> alpha.run()",
                  "alpha example one", source_id=sid,
                  metadata={"evidence_references":
                            [{"document": "library/alpha.rst"}]})
    repo.add_node("ex-tie-2", "example", ">>> alpha.run() again",
                  "second alpha example with alpha word", source_id=sid)
    repo.add_node("dep-import-alpha", "dependency", "import alpha",
                  "imports alpha", source_id=None)   # no provenance at all
    repo.add_node("lone-concept", "concept", "Isolated Notion",
                  "mentions nothing useful")
    rels = [
        ("ex-tie-1", "example_of", "mod-alpha", "inferred:test_rule"),
        ("ex-tie-2", "example_of", "mod-alpha", None),
        ("dep-import-alpha", "depends_on", "mod-alpha", None),
        ("dep-import-alpha", "references", "ghost-dangling-target", None),
        ("mod-beta", "extends", "mod-alpha", "beta extends"),
        ("mod-beta", "related_to", "mod-gamma", "néighbour"),
        ("mod-beta", "uses", "mod-alpha", None),
    ]
    for src, rtype, tgt, label in rels:
        repo.add_relationship(src, rtype, tgt, label)
    return repo


class V11SearchEquivalenceTests(unittest.TestCase):
    """Optimized paths must be byte-identical to the legacy algorithms."""

    def setUp(self):
        self.repo = build_fixture()
        self.api = KnowledgeAPI(store=KnowledgeStore(repository=self.repo))

    def tearDown(self):
        self.api.close()

    def test_repo_search_matches_legacy_exactly(self):
        queries = ["alpha", "beta", "gamma", "", "zzz-absent",
                   "ALPHA Beta", "  spaces   ", "évidence"]
        for q in queries:
            for limit in (None, 1, 2, 3, 10):
                with self.subTest(query=q, limit=limit):
                    got = self.repo.search_nodes(q, limit=limit)
                    want = legacy_search_nodes(self.repo, q, limit=limit)
                    self.assertEqual(_bytes(want), _bytes(got))

    def test_api_search_matches_legacy_exactly(self):
        cases = [
            ("alpha", None, None),
            ("alpha", None, 1),
            ("alpha", "technology", None),
            ("alpha", "example", 1),
            ("module", "technology", 2),
            ("nothing-matches-here", None, None),
        ]
        for q, ntype, lim in cases:
            with self.subTest(query=q, type=ntype, limit=lim):
                got = self.api.search(q, node_type=ntype, limit=lim)
                want = legacy_api_search(self.api, q, ntype, lim)
                self.assertEqual(_bytes(want), _bytes(got))

    def test_tie_ordering_is_stable_and_deterministic(self):
        first = self.api.search("alpha", limit=10)
        for _ in range(4):
            self.assertEqual(_bytes(first), _bytes(self.api.search(
                "alpha", limit=10)))
        scores = [n["_score"] for n in first]
        self.assertEqual(scores, sorted(scores, reverse=True))
        # equal-score nodes stay ordered by id (api-level determinism rule)
        pairs = [(n["_score"], n["id"]) for n in first]
        for (s1, i1), (s2, i2) in zip(pairs, pairs[1:]):
            if s1 == s2:
                self.assertLessEqual(i1, i2)

    def test_hydrate_nodes_preserves_requested_order(self):
        got = self.repo.hydrate_nodes(
            ["mod-beta", "mod-alpha", "missing-id", "dep-import-alpha"])
        self.assertEqual([n["id"] for n in got],
                         ["mod-beta", "mod-alpha", "dep-import-alpha"])

    def test_search_rankings_shape_and_order(self):
        ranked = self.repo.search_rankings("alpha beta")
        self.assertTrue(all(isinstance(r, tuple) and len(r) == 3
                            for r in ranked))
        known_ids = {row[0] for row in self.repo.conn.execute(
            "SELECT id FROM nodes")}
        for score, nid, ntype in ranked:
            self.assertGreaterEqual(score, 1)
            self.assertIn(nid, known_ids)
            self.assertIn(ntype, {"concept", "technology", "entity",
                                  "procedure", "rule", "example",
                                  "dependency"})
        # scan order (stable input for caller-side deterministic sorting);
        # compare against the SAME projection _scan_scores uses, because a
        # bare "SELECT id" would walk the primary-key index instead
        ids_in_scan = [nid for _, nid, _ in ranked]
        scan_order = [row[0] for row in self.repo.conn.execute(
            "SELECT id, type, name, description, source_id, metadata "
            "FROM nodes")]
        self.assertEqual(ids_in_scan,
                         [i for i in scan_order if i in set(ids_in_scan)])


class V11QueryCollapseTests(unittest.TestCase):
    """N+1 patterns must be gone: bounded query counts everywhere."""

    def setUp(self):
        self.repo = build_fixture()
        # amplify: many matching nodes so legacy counts would scale linearly
        for i in range(60):
            self.repo.add_node(f"bulk-{i:03d}", "entity", f"bulk alpha {i}",
                               f"alpha bulk row {i}")
        self.api = KnowledgeAPI(store=KnowledgeStore(repository=self.repo))

    def tearDown(self):
        self.api.close()

    def test_api_search_query_count_is_bounded(self):
        with QueryCounter(self.repo) as qc:
            self.api.search("alpha", limit=20)
        # scan + hydration(nodes+sources+relationships) ~= 4; allow slack
        self.assertLessEqual(qc.selects, 8)
        self.assertGreater(qc.selects, 0)

    def test_repo_limited_search_does_not_hydrate_every_match(self):
        with QueryCounter(self.repo) as qc:
            self.repo.search_nodes("alpha", limit=5)
        # legacy would issue 2 queries per match (~65 matches -> 130);
        # optimized hydrates only the returned 5.
        self.assertLessEqual(qc.selects, 15)

    def test_load_from_repository_query_count_is_bounded(self):
        store = KnowledgeStore(repository=self.repo)
        with QueryCounter(self.repo) as qc:
            store.load_from_repository()
        self.assertLessEqual(qc.selects, 5)
        self.assertEqual(len(store.nodes), 67)

    def test_only_final_top_n_nodes_are_hydrated(self):
        seen = []
        original = self.repo._hydrate_rows

        def spy(rows, with_relationships=False):
            seen.append([r["id"] for r in rows])
            return original(rows, with_relationships=with_relationships)

        self.repo._hydrate_rows = spy
        hits = self.api.search("alpha", limit=7)
        self.assertEqual(len(hits), 7)
        self.assertEqual(len(seen[-1]), 7)


class V11LoadFromRepositoryEquivalenceTests(unittest.TestCase):
    """Mirror built by load_from_repository equals legacy construction."""

    def setUp(self):
        self.repo = build_fixture()

    def test_mirror_equals_legacy_per_node_construction(self):
        store = KnowledgeStore(repository=self.repo)
        store.load_from_repository()
        legacy_nodes = []
        rows = self.repo.conn.execute("SELECT * FROM nodes").fetchall()
        for row in rows:
            legacy_nodes.append(self.repo._node_dict(row,
                                                     with_relationships=True))
        self.assertEqual(_bytes(store.all()), _bytes(legacy_nodes))
        self.assertEqual(store._order,
                         [row["id"] for row in rows])

    def test_reload_is_idempotent(self):
        store = KnowledgeStore(repository=self.repo)
        store.load_from_repository()
        first = _bytes(store.all())
        store.load_from_repository()
        self.assertEqual(first, _bytes(store.all()))

    def test_chunked_bulk_queries_cross_the_param_boundary(self):
        """>500 nodes/relationships force multiple IN-list chunks; grouping
        and per-node ordering must remain identical to legacy."""
        repo = KnowledgeRepository(":memory:").initialize()
        sid = repo.add_source("bulk-src", version="1.0", location="b.json")
        for i in range(650):
            repo.add_node(f"n-{i:04d}", "entity", f"node {i}",
                          f"alpha bulk node {i}", source_id=sid)
            repo.add_relationship(f"n-{i:04d}", "references",
                                  f"n-{(i + 1) % 650:04d}",
                                  None if i % 3 else f"label {i}")
        store = KnowledgeStore(repository=repo)
        store.load_from_repository()
        legacy_nodes = [repo._node_dict(row, with_relationships=True)
                        for row in repo.conn.execute(
                            "SELECT * FROM nodes").fetchall()]
        self.assertEqual(len(store.nodes), 650)
        self.assertEqual(_bytes(store.all()), _bytes(legacy_nodes))
        # search hydration also spans chunks and stays byte-equal
        got = repo.search_nodes("alpha", limit=700)
        self.assertEqual(_bytes(got),
                         _bytes(legacy_search_nodes(repo, "alpha",
                                                    limit=700)))

    def test_dangling_targets_survive_the_mirror(self):
        store = KnowledgeStore(repository=self.repo)
        store.load_from_repository()
        rels = store.get("dep-import-alpha")["relationships"]
        self.assertIn(("references", "ghost-dangling-target"),
                      [(r["type"], r["target"]) for r in rels])


class V11ProductionSafetyTests(unittest.TestCase):
    """Production DB is never written; Contract v1 stays frozen."""

    @unittest.skipUnless(os.path.exists(DEFAULT_KNOWLEDGE_DB),
                         "production db not present")
    def test_production_db_read_only_across_searches(self):
        sha_before = hashlib.sha256(
            open(DEFAULT_KNOWLEDGE_DB, "rb").read()).hexdigest()
        api = KnowledgeAPI(db_path=DEFAULT_KNOWLEDGE_DB)
        try:
            api.search("python", limit=3)
            api.search("import glob", limit=3)
            api.inspect()
        finally:
            api.close()
        con = sqlite3.connect(f"file:{DEFAULT_KNOWLEDGE_DB}?mode=ro",
                              uri=True)
        try:
            self.assertEqual(con.execute("PRAGMA integrity_check")
                             .fetchone()[0], "ok")
            self.assertEqual(con.execute("PRAGMA foreign_key_check")
                             .fetchall(), [])
        finally:
            con.close()
        sha_after = hashlib.sha256(
            open(DEFAULT_KNOWLEDGE_DB, "rb").read()).hexdigest()
        self.assertEqual(sha_before, sha_after)

    def test_contract_v1_untouched(self):
        self.assertEqual(CONTRACT_VERSION, "1")
        repo = build_fixture()
        store = KnowledgeStore(repository=repo)
        api = KnowledgeAPI(store=store)
        tool = ToolInterface(api=api)
        resp = tool.execute({"operation": "search",
                             "arguments": {"query": "alpha", "limit": 2}})
        self.assertEqual(resp.get("contract_version"), "1")
        self.assertTrue(resp.get("ok"))
        self.assertEqual(resp["operation"], "search")
        api.close()
        if getattr(tool, "_owns_api", False):
            pass

    def test_error_behavior_unchanged(self):
        api = KnowledgeAPI(store=KnowledgeStore(repository=build_fixture()))
        try:
            with self.assertRaises(Exception) as ctx:
                api.search("x", node_type="nope")
            self.assertIn("invalid node type", str(ctx.exception))
            with self.assertRaises(Exception) as ctx:
                api.search("x", limit=0)
            self.assertIn("limit must be a positive integer",
                          str(ctx.exception))
            with self.assertRaises(Exception) as ctx:
                api.search(123)
            self.assertIn("query must be a string", str(ctx.exception))
        finally:
            api.close()


if __name__ == "__main__":
    unittest.main()
