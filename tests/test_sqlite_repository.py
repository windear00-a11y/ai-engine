import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from retrieval.repository import KnowledgeRepository
from retrieval.migration import migrate_json_to_sqlite
from retrieval.knowledge import KnowledgeStore, VALID_TYPES

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Generic knowledge corpus (transport domain) used by migration tests. The
# react corpus that previously lived at <repo>/knowledge is react/demo data,
# retired with its domain in Phase 24.
KNOWLEDGE_DIR = os.path.join(_ROOT, "tests", "fixtures", "knowledge_generic")


def sample_node(nid, ntype="concept", rels=None):
    return {
        "id": nid, "name": nid.title(), "type": ntype,
        "description": f"node {nid}",
        "relationships": rels or [],
    }


class RepositoryInitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "k.db")
        self.repo = KnowledgeRepository(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_initialize_creates_tables(self):
        self.repo.initialize()
        tables = [r[0] for r in self.repo.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for t in ("sources", "nodes", "relationships"):
            self.assertIn(t, tables)

    def test_foreign_keys_enabled(self):
        self.repo.initialize()
        cur = self.repo.conn.execute("PRAGMA foreign_keys")
        self.assertEqual(cur.fetchone()[0], 1)

    def test_primary_keys_enforced(self):
        self.repo.initialize()
        self.repo.add_source("s")
        with self.repo.transaction():
            self.repo.conn.execute(
                "INSERT INTO nodes (id, type) VALUES (?, ?)", ("a", "concept"))
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.conn.execute(
                "INSERT INTO nodes (id, type) VALUES (?, ?)", ("a", "concept"))


class SourceNodeRelationshipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = KnowledgeRepository(os.path.join(self.tmp.name, "k.db"))
        self.repo.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_source_insertion_and_get(self):
        sid = self.repo.add_source("file.json", location="/x/file.json",
                                   metadata={"k": "v"})
        src = self.repo.get_source(sid)
        self.assertEqual(src["name"], "file.json")
        self.assertEqual(json.loads(src["metadata"])["k"], "v")
        self.assertTrue(src["imported_at"])

    def test_node_insertion_and_get(self):
        self.repo.add_source("s")
        self.repo.add_node("n1", "concept", "N1", "desc", metadata={"cat": "x"})
        node = self.repo.get_node("n1")
        self.assertEqual(node["name"], "N1")
        self.assertEqual(node["cat"], "x")  # extra fields preserved

    def test_duplicate_node_rejected(self):
        self.repo.add_source("s")
        self.repo.add_node("n1", "concept", "N1", "d")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.add_node("n1", "concept", "N1", "d")

    def test_relationship_insertion(self):
        self.repo.add_source("s")
        self.repo.add_node("a", "concept", "A", "d")
        self.repo.add_node("b", "concept", "B", "d")
        self.repo.add_relationship("a", "related_to", "b", "label")
        rels = self.repo.relationships_of("a")
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]["target"], "b")

    def test_invalid_relationship_rejected_empty_target(self):
        self.repo.add_source("s")
        self.repo.add_node("a", "concept", "A", "d")
        with self.assertRaises(ValueError):
            self.repo.add_relationship("a", "related_to", "")

    def test_invalid_relationship_rejected_missing_source(self):
        with self.assertRaises(ValueError):
            self.repo.add_relationship("ghost", "related_to", "b")

    def test_foreign_key_source_enforced(self):
        # node with a non-existent source_id must violate FK
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.add_node("n1", "concept", "N", None, source_id=999)

    def test_duplicate_relationship_rejected(self):
        self.repo.add_source("s")
        self.repo.add_node("a", "concept", "A", "d")
        self.repo.add_node("b", "concept", "B", "d")
        self.repo.add_relationship("a", "related_to", "b")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.add_relationship("a", "related_to", "b")

    def test_forward_references_allowed(self):
        # target need not exist (knowledge graph dangling refs)
        self.repo.add_source("s")
        self.repo.add_node("a", "concept", "A", "d")
        self.repo.add_relationship("a", "related_to", "nonexistent")
        self.assertEqual(len(self.repo.relationships_of("a")), 1)


class TransactionRollbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = KnowledgeRepository(os.path.join(self.tmp.name, "k.db"))
        self.repo.initialize()

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_node_rolls_back_on_bad_relationship(self):
        # node 'a' with one valid rel and one invalid (empty target) rel
        data = sample_node("a", rels=[
            {"type": "related_to", "target": "b"},
            {"type": "related_to", "target": ""},
        ])
        with self.assertRaises(ValueError):
            self.repo.import_node("file.json", "/x/file.json", data)
        # nothing should have been persisted
        self.assertIsNone(self.repo.get_node("a"))
        self.assertEqual(self.repo.count_nodes(), 0)
        self.assertEqual(self.repo.count_relationships(), 0)

    def test_import_node_atomic_success(self):
        data = sample_node("a", rels=[{"type": "related_to", "target": "b"}])
        self.repo.import_node("file.json", "/x/file.json", data)
        self.assertEqual(self.repo.count_nodes(), 1)
        self.assertEqual(self.repo.count_relationships(), 1)


class SearchAndTraversalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = KnowledgeRepository(os.path.join(self.tmp.name, "k.db"))
        self.repo.initialize()
        self.repo.add_source("s")
        self.repo.add_node("a", "concept", "Apple", "red fruit")
        self.repo.add_node("b", "concept", "Banana", "yellow fruit")
        self.repo.add_node("c", "concept", "Cherry", "red fruit")
        self.repo.add_relationship("a", "related_to", "b")
        self.repo.add_relationship("b", "related_to", "c")

    def tearDown(self):
        self.tmp.cleanup()

    def test_count(self):
        self.assertEqual(self.repo.count_nodes(), 3)
        self.assertEqual(self.repo.count_relationships(), 2)

    def test_search(self):
        res = self.repo.search_nodes("fruit")
        ids = {n["id"] for n in res}
        self.assertEqual(ids, {"a", "b", "c"})

    def test_search_ranking(self):
        res = self.repo.search_nodes("red")
        # 'a' and 'c' mention red; 'a' (Apple) and 'c' (Cherry) both; ranking stable
        self.assertTrue(all(n["_score"] >= 1 for n in res))

    def test_relationships_of(self):
        rels = self.repo.relationships_of("a")
        self.assertEqual(rels[0]["target"], "b")

    def test_follow_chain(self):
        pairs = self.repo.follow("a")
        targets = {t["id"] for _, t in pairs}
        self.assertEqual(targets, {"b"})

    def test_follow_with_type_filter(self):
        pairs = self.repo.follow("a", rel_type="related_to")
        self.assertEqual({t["id"] for _, t in pairs}, {"b"})


class PersistenceAfterReopenTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "k.db")
        repo = KnowledgeRepository(self.db)
        repo.initialize()
        repo.add_source("s")
        repo.add_node("persist", "concept", "Persist", "survives reopen")
        repo.add_relationship("persist", "related_to", "persist")
        repo.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_data_survives_reopen(self):
        repo = KnowledgeRepository(self.db)
        repo.initialize()
        self.assertEqual(repo.get_node("persist")["name"], "Persist")
        self.assertEqual(repo.count_nodes(), 1)
        self.assertEqual(len(repo.relationships_of("persist")), 1)


class SqlInjectionSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = KnowledgeRepository(os.path.join(self.tmp.name, "k.db"))
        self.repo.initialize()
        self.repo.add_source("s")

    def tearDown(self):
        self.tmp.cleanup()

    def test_malicious_id_stored_literally(self):
        evil = "x'; DROP TABLE nodes;--"
        self.repo.add_node(evil, "concept", "Evil", "desc")
        node = self.repo.get_node(evil)
        self.assertIsNotNone(node)
        self.assertEqual(node["id"], evil)

    def test_injection_query_does_not_drop(self):
        self.repo.add_node("ok", "concept", "OK", "desc")
        # a query that looks like SQL must be treated as a literal search term
        res = self.repo.search_nodes("DROP TABLE nodes")
        self.assertEqual(res, [])
        # table still exists
        tables = [r[0] for r in self.repo.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        self.assertIn("nodes", tables)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "migrated.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrate_generic_corpus(self):
        summary = migrate_json_to_sqlite(KNOWLEDGE_DIR, self.db, clear=True)
        self.assertEqual(summary["errors"], [])
        self.assertEqual(summary["sources"], 4)
        self.assertEqual(summary["nodes"], 4)
        self.assertEqual(summary["relationships"], 6)

        repo = KnowledgeRepository(self.db)
        repo.initialize()
        # Transport retrievable
        transport = repo.get_node("transport")
        self.assertIsNotNone(transport)
        self.assertEqual(transport["type"], "technology")
        # Transport Component retrievable
        rc = repo.get_node("transport-component")
        self.assertIsNotNone(rc)
        self.assertEqual(rc["type"], "concept")
        # relationships present
        rels = {r["type"]: r["target"] for r in repo.relationships_of("transport-component")}
        self.assertEqual(rels["instance_of"], "transport")
        # follow works
        followed = repo.follow("transport-component", rel_type="instance_of")
        self.assertEqual(followed[0][1]["id"], "transport")
        self.assertEqual(repo.count_nodes(), 4)
        self.assertEqual(repo.count_relationships(), 6)

    def test_migrated_knowledge_works_with_tools(self):
        migrate_json_to_sqlite(KNOWLEDGE_DIR, self.db, clear=True)
        # Build a store backed by the migrated SQLite database.
        store = KnowledgeStore(db_path=self.db).load_from_repository()
        from tools.knowledge_tools import KnowledgeTools
        tools = KnowledgeTools(store=store)
        self.assertTrue(tools.get("transport")["found"])
        res = tools.search("transport component")
        self.assertGreater(res["count"], 0)
        follow = tools.follow("transport-component", rel_type="instance_of")
        self.assertIn("transport", {n["id"] for n in follow["nodes"]})


if __name__ == "__main__":
    unittest.main()
