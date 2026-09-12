"""Tests for the Knowledge API (deterministic, read-only).

Covers: search (with type filter, limits, empty query), get, related, follow,
provenance, inspect, missing-node errors, deterministic ordering, in-memory
isolation, and the guarantee that read operations never mutate the database.

Every test uses an isolated temporary or in-memory database; no committed
repository database is required.
"""

import hashlib
import os
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api import (
    KnowledgeAPI, NodeNotFoundError, KnowledgeArgumentError,
    RelationshipTypeError,
)
from retrieval.knowledge import KnowledgeStore
from retrieval.repository import KnowledgeRepository


def _seed(repo):
    repo.initialize()
    sid = repo.add_source("test-source", version="1.0",
                          location="/tmp/test-source.json")
    repo.add_node("exceptions", "concept", "Python Exceptions",
                  "Error handling concepts", source_id=sid)
    repo.add_node("exception-class", "entity", "Exception",
                  "Base exception class", source_id=sid)
    repo.add_node("keyboard", "entity", "KeyboardInterrupt",
                  "A user-interrupt exception", source_id=sid)
    repo.add_node("floats", "concept", "Floating point arithmetic",
                  "Numbers with a fractional part", source_id=sid)
    repo.add_node("example", "example", "try: x()",
                  "Exception example snippet", source_id=sid)
    repo.add_relationship("exception-class", "extends", "exceptions", "lbl")
    repo.add_relationship("keyboard", "extends", "exceptions", "lbl")
    repo.add_relationship("example", "example_of", "exceptions", "lbl")


def _seed_file(db_path):
    repo = KnowledgeRepository(db_path)
    _seed(repo)
    repo.close()


def _api_from_file(db_path):
    api = KnowledgeAPI(db_path=db_path)
    return api


def _api_in_memory():
    repo = KnowledgeRepository(":memory:")
    _seed(repo)
    return KnowledgeAPI(store=KnowledgeStore(repository=repo))


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class FileDBTestCase(unittest.TestCase):
    """Base: a temporary file-backed DB, closed and cleaned per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = os.path.join(self._tmp.name, "knowledge.db")
        _seed_file(self._db)
        self.api = _api_from_file(self._db)

    def tearDown(self):
        self.api.close()
        self._tmp.cleanup()


class SearchTests(FileDBTestCase):
    def test_search_finds_matching_nodes(self):
        results = self.api.search("exception")
        ids = [r["id"] for r in results]
        self.assertIn("exceptions", ids)
        self.assertIn("exception-class", ids)

    def test_search_empty_query_returns_empty(self):
        self.assertEqual(self.api.search(""), [])
        self.assertEqual(self.api.search("   "), [])

    def test_search_no_match_returns_empty(self):
        self.assertEqual(self.api.search("zzzznomatch"), [])

    def test_search_node_type_filter(self):
        entities = self.api.search("exception", node_type="entity")
        self.assertTrue(entities)
        self.assertTrue(all(n["type"] == "entity" for n in entities))
        concepts = self.api.search("exception", node_type="concept")
        self.assertTrue(all(n["type"] == "concept" for n in concepts))
        self.assertIn("exceptions", [n["id"] for n in concepts])

    def test_search_limit(self):
        limited = self.api.search("exception", limit=1)
        self.assertEqual(len(limited), 1)

    def test_search_deterministic_ordering(self):
        a = [r["id"] for r in self.api.search("exception")]
        b = [r["id"] for r in self.api.search("exception")]
        self.assertEqual(a, b)
        scores = self.api.store.repo.search_nodes("exception", limit=None)
        highest = max(n.get("_score", 0) for n in scores)
        top = [r["id"] for r in self.api.search("exception")
               if r.get("_score", 0) == highest]
        self.assertEqual(top, sorted(top))

    def test_search_returns_serializable_nodes(self):
        for r in self.api.search("exception"):
            for key in ("id", "type", "name"):
                self.assertIn(key, r)

    def test_search_invalid_node_type_raises(self):
        with self.assertRaises(KnowledgeArgumentError):
            self.api.search("x", node_type="not_a_type")

    def test_search_invalid_limit_raises(self):
        with self.assertRaises(KnowledgeArgumentError):
            self.api.search("x", limit=0)
        with self.assertRaises(KnowledgeArgumentError):
            self.api.search("x", limit=-3)


class GetTests(FileDBTestCase):
    def test_get_returns_node_with_metadata_and_relationships(self):
        node = self.api.get("example")
        self.assertEqual(node["id"], "example")
        self.assertEqual(node["type"], "example")
        self.assertIn("name", node)
        self.assertIn("description", node)
        self.assertIsNotNone(node.get("provenance"))
        rels = {r["type"] for r in node.get("relationships", [])}
        self.assertIn("example_of", rels)

    def test_get_missing_node_raises(self):
        with self.assertRaises(NodeNotFoundError):
            self.api.get("nope")

    def test_get_rejects_empty_id(self):
        with self.assertRaises(KnowledgeArgumentError):
            self.api.get("")


class RelatedTests(FileDBTestCase):
    def test_related_returns_deterministic_neighbours(self):
        rel = self.api.related("exceptions")
        ids = [r["node"]["id"] for r in rel]
        self.assertEqual(ids, sorted(ids))  # deterministic order
        self.assertIn("exception-class", ids)
        self.assertIn("example", ids)

    def test_related_includes_both_directions(self):
        rel = self.api.related("exception-class")
        self.assertEqual(rel[0]["node"]["id"], "exceptions")
        via = rel[0]["via"]
        types = {v["relationship_type"] for v in via}
        self.assertIn("extends", types)
        self.assertIn("outgoing", [v["direction"] for v in via])

    def test_related_limit(self):
        self.assertEqual(len(self.api.related("exceptions", limit=1)), 1)

    def test_related_missing_node_raises(self):
        with self.assertRaises(NodeNotFoundError):
            self.api.related("nope")


class FollowTests(FileDBTestCase):
    def test_follow_returns_traversal(self):
        out = self.api.follow("exception-class")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["relationship_type"], "extends")
        self.assertEqual(out[0]["target_node_id"], "exceptions")
        self.assertEqual(out[0]["node"]["id"], "exceptions")

    def test_follow_type_filter(self):
        self.assertEqual(len(self.api.follow("exception-class", "extends")), 1)
        self.assertEqual(
            len(self.api.follow("exception-class", "related_to")), 0)

    def test_follow_invalid_type_raises(self):
        with self.assertRaises(RelationshipTypeError):
            self.api.follow("exception-class", "bogus_edge")

    def test_follow_deterministic(self):
        a = [r["target_node_id"] for r in self.api.follow("exceptions")]
        b = [r["target_node_id"] for r in self.api.follow("exceptions")]
        self.assertEqual(a, b)

    def test_follow_missing_node_raises(self):
        with self.assertRaises(NodeNotFoundError):
            self.api.follow("nope")


class InspectTests(FileDBTestCase):
    def test_inspect_counts(self):
        info = self.api.inspect()
        self.assertEqual(info["source_count"], 1)
        self.assertEqual(info["node_count"], 5)
        self.assertEqual(info["relationship_count"], 3)
        self.assertEqual(sum(info["nodes_by_type"].values()), 5)
        self.assertEqual(sum(info["relationships_by_type"].values()), 3)
        self.assertEqual(info["nodes_by_type"]["concept"], 2)
        self.assertEqual(info["nodes_by_type"]["entity"], 2)


class ProvenanceTests(FileDBTestCase):
    def test_provenance_returns_source_and_evidence(self):
        prov = self.api.provenance("exceptions")
        self.assertEqual(prov["node_id"], "exceptions")
        self.assertEqual(prov["source_name"], "test-source")
        self.assertEqual(prov["source_version"], "1.0")
        self.assertEqual(prov["source_location"], "/tmp/test-source.json")
        self.assertIsNotNone(prov["source_id"])
        self.assertIn("imported_at", prov)

    def test_provenance_missing_node_raises(self):
        with self.assertRaises(NodeNotFoundError):
            self.api.provenance("nope")


class IsolationTests(unittest.TestCase):
    def test_in_memory_isolation(self):
        a = _api_in_memory()
        b = _api_in_memory()
        try:
            self.assertEqual(a.inspect()["node_count"],
                             b.inspect()["node_count"])
            self.assertNotEqual(id(a.store.repo.conn), id(b.store.repo.conn))
        finally:
            a.close()
            b.close()

    def test_api_does_not_expose_raw_connection(self):
        api = _api_in_memory()
        try:
            self.assertFalse(hasattr(api, "conn"))
            self.assertFalse(hasattr(api, "cursor"))
        finally:
            api.close()


class ReadOnlyTests(unittest.TestCase):
    def test_read_operations_do_not_mutate_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            _seed_file(db)
            before_hash = _sha256(db)
            api = _api_from_file(db)
            api.search("exception")
            api.get("exceptions")
            api.related("exceptions")
            api.follow("exception-class")
            api.provenance("exceptions")
            api.inspect()
            api.close()
            self.assertEqual(_sha256(db), before_hash)


if __name__ == "__main__":
    unittest.main()