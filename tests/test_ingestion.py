import json
import os
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import ingestion.source_format as sf
from ingestion.validator import validate_source, ValidationResult
from ingestion.importer import import_source_file, import_source_data, ImportSummary
from retrieval.repository import KnowledgeRepository, DEFAULT_KNOWLEDGE_DB
from retrieval.knowledge import KnowledgeStore, VALID_TYPES
from tools.knowledge_tools import KnowledgeTools

EXAMPLE_CORPUS = os.path.join(_ROOT, "ingestion", "examples", "python_core.json")

_NODE_TYPES = ["concept", "technology", "entity", "procedure",
               "rule", "example", "dependency"]


def write_json(tmp, name, obj):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return path


def fresh_repo():
    d = tempfile.mkdtemp()
    repo = KnowledgeRepository(os.path.join(d, "k.db"))
    repo.initialize()
    return repo


class ValidatorValidSourceTests(unittest.TestCase):
    def test_valid_source_passes(self):
        data = sf.build_source("s", [sf.build_node("a", "concept", "A", "desc")])
        res = validate_source(data)
        self.assertTrue(res.valid)
        self.assertEqual(res.errors, [])
        self.assertEqual(res.source["name"], "s")
        self.assertEqual(len(res.nodes), 1)

    def test_every_node_type_valid(self):
        for t in _NODE_TYPES:
            data = sf.build_source("s", [sf.build_node("n", t, "N", "d")])
            res = validate_source(data)
            self.assertTrue(res.valid, f"type {t} should validate")

    def test_relationships_within_source(self):
        data = sf.build_source("s", [
            sf.build_node("a", "concept", "A", "d",
                          relationships=[{"type": "related_to", "target": "b"}]),
            sf.build_node("b", "concept", "B", "d"),
        ])
        res = validate_source(data)
        self.assertTrue(res.valid)


class ValidatorInvalidSourceTests(unittest.TestCase):
    def test_invalid_root(self):
        res = validate_source([1, 2, 3])
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "invalid_root" for e in res.errors))

    def test_malformed_source_metadata_not_object(self):
        res = validate_source({"source": "nope", "nodes": []})
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "source_metadata"
                            and e.path == "source" for e in res.errors))

    def test_source_name_missing(self):
        res = validate_source({"source": {"version": "1"}, "nodes": []})
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "source_metadata"
                            and e.path == "source.name" for e in res.errors))

    def test_source_version_not_string(self):
        res = validate_source({"source": {"name": "x", "version": 3}, "nodes": []})
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "source_metadata"
                            and e.path == "source.version" for e in res.errors))

    def test_duplicate_ids(self):
        data = sf.build_source("s", [
            sf.build_node("dup", "concept", "A", "d"),
            sf.build_node("dup", "concept", "B", "d"),
        ])
        res = validate_source(data)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "duplicate_id" for e in res.errors))

    def test_invalid_node_type(self):
        data = sf.build_source("s", [sf.build_node("a", "bogus", "A", "d")])
        res = validate_source(data)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "node_type" for e in res.errors))

    def test_missing_fields(self):
        # missing id, name, type, description individually
        for missing in ("id", "name", "type", "description"):
            node = {"id": "a", "type": "concept", "name": "A", "description": "d"}
            node.pop(missing)
            res = validate_source(sf.build_source("s", [node]))
            self.assertFalse(res.valid, f"missing {missing} should fail")
            codes = {e.code for e in res.errors}
            self.assertTrue(
                {"node_id", "node_name", "node_type", "node_description"}
                & codes)

    def test_invalid_relationship_missing_target(self):
        data = sf.build_source("s", [sf.build_node(
            "a", "concept", "A", "d",
            relationships=[{"type": "related_to"}])])
        res = validate_source(data)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "relationship_target" for e in res.errors))

    def test_invalid_relationship_not_object(self):
        data = sf.build_source("s", [sf.build_node(
            "a", "concept", "A", "d", relationships=["nope"])])
        res = validate_source(data)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "relationship_structure"
                            for e in res.errors))

    def test_invalid_relationship_not_list(self):
        data = sf.build_source("s", [sf.build_node(
            "a", "concept", "A", "d", relationships={"type": "x", "target": "b"})])
        res = validate_source(data)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "relationship_structure"
                            for e in res.errors))

    def test_relationship_target_not_in_source(self):
        data = sf.build_source("s", [sf.build_node(
            "a", "concept", "A", "d",
            relationships=[{"type": "related_to", "target": "ghost"}])])
        res = validate_source(data)
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "relationship_target"
                            and "ghost" in e.message for e in res.errors))

    def test_empty_nodes_list(self):
        res = validate_source(sf.build_source("s", []))
        self.assertFalse(res.valid)
        self.assertTrue(any(e.code == "nodes" for e in res.errors))


class NormalizationTests(unittest.TestCase):
    def test_id_trimmed_and_type_lowercased(self):
        data = sf.build_source("s", [{
            "id": "  Foo  ", "type": "Concept", "name": "Foo", "description": "d"}])
        res = validate_source(data)
        self.assertTrue(res.valid)
        self.assertEqual(res.nodes[0]["id"], "Foo")       # trimmed, case kept
        self.assertEqual(res.nodes[0]["type"], "concept")  # type normalized

    def test_relationship_label_defaults_none(self):
        data = sf.build_source("s", [
            sf.build_node("a", "concept", "A", "d",
                          relationships=[{"type": "related_to", "target": "b"}]),
            sf.build_node("b", "concept", "B", "d")])
        res = validate_source(data)
        self.assertTrue(res.valid)
        self.assertEqual(res.nodes[0]["relationships"][0]["label"], None)

    def test_extras_preserved(self):
        data = sf.build_source("s", [sf.build_node(
            "a", "concept", "A", "d", code="x = 1")])
        res = validate_source(data)
        self.assertTrue(res.valid)
        self.assertEqual(res.nodes[0]["_extras"]["code"], "x = 1")


class ImporterAtomicityTests(unittest.TestCase):
    def test_validation_failure_touches_no_db(self):
        repo = fresh_repo()
        data = sf.build_source("s", [sf.build_node(
            "a", "concept", "A", "d",
            relationships=[{"type": "related_to", "target": "ghost"}])])
        summary = import_source_data(repo, data)
        self.assertFalse(summary.valid)
        self.assertEqual(repo.count_nodes(), 0)
        self.assertEqual(repo.count_relationships(), 0)
        self.assertEqual(repo.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 0)

    def test_import_failure_rolls_back_partial_source(self):
        repo = fresh_repo()
        # First, a valid source that occupies id "a".
        ok = import_source_data(repo, sf.build_source(
            "s1", [sf.build_node("a", "concept", "A", "d")]))
        self.assertTrue(ok.valid)
        self.assertEqual(repo.count_nodes(), 1)
        self.assertEqual(repo.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 1)
        # Second source is valid on its own but collides on node id "a".
        bad = import_source_data(repo, sf.build_source(
            "s2", [sf.build_node("a", "concept", "A", "d"),
                   sf.build_node("b", "concept", "B", "d")]))
        self.assertFalse(bad.valid)
        self.assertTrue(any(e["code"] == "import_failed" for e in bad.errors))
        # Nothing partial: no new source, still 1 node, 0 relationships.
        self.assertEqual(repo.count_nodes(), 1)
        self.assertEqual(repo.count_relationships(), 0)
        self.assertEqual(repo.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0], 1)


class ProvenanceTests(unittest.TestCase):
    def test_provenance_retained_and_not_invented(self):
        d = tempfile.mkdtemp()
        path = write_json(d, "src.json", sf.build_source(
            "prov", [sf.build_node("a", "concept", "A", "d")],
            version="2.1", location="file:///real/loc"))
        repo = fresh_repo()
        summary = import_source_file(repo, path)
        self.assertTrue(summary.valid)
        src = repo.get_source(summary.source_id)
        self.assertEqual(src["name"], "prov")
        self.assertEqual(src["version"], "2.1")          # provided
        self.assertEqual(src["location"], "file:///real/loc")  # provided, not invented
        node = repo.get_node("a")
        self.assertEqual(node["source_id"], summary.source_id)
        self.assertEqual(node["provenance"]["source_name"], "prov")
        self.assertEqual(node["provenance"]["source_version"], "2.1")
        self.assertEqual(node["provenance"]["source_location"], "file:///real/loc")
        self.assertIsNotNone(node["provenance"]["imported_at"])

    def test_missing_version_is_none_not_invented(self):
        d = tempfile.mkdtemp()
        # no version, no location -> location falls back to real file path
        path = write_json(d, "src.json", sf.build_source(
            "nov", [sf.build_node("a", "concept", "A", "d")]))
        repo = fresh_repo()
        summary = import_source_file(repo, path)
        self.assertTrue(summary.valid)
        src = repo.get_source(summary.source_id)
        self.assertIsNone(src["version"])               # not invented
        self.assertEqual(src["location"], path)         # real file path

    def test_knowledgetools_exposes_provenance(self):
        repo = fresh_repo()
        import_source_data(repo, sf.build_source(
            "pt", [sf.build_node("a", "concept", "A", "d")], version="1.0"))
        store = KnowledgeStore(repository=repo).load_from_repository()
        tools = KnowledgeTools(store=store)
        view = tools.get("a")
        self.assertTrue(view["found"])
        self.assertIsNotNone(view["provenance"])
        self.assertEqual(view["provenance"]["source_name"], "pt")


class CLITests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "ingestion", *args],
            cwd=_ROOT, capture_output=True, text=True)

    def test_cli_validate_valid(self):
        r = self._run("validate", EXAMPLE_CORPUS)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertTrue(out["valid"])
        self.assertEqual(out["node_count"], 22)

    def test_cli_validate_invalid(self):
        d = tempfile.mkdtemp()
        path = write_json(d, "bad.json", sf.build_source(
            "s", [sf.build_node("a", "bogus", "A", "d")]))
        r = self._run("validate", path)
        self.assertEqual(r.returncode, 1)
        out = json.loads(r.stdout)
        self.assertFalse(out["valid"])
        self.assertTrue(out["errors"])

    def test_cli_inspect(self):
        r = self._run("inspect", EXAMPLE_CORPUS)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertEqual(out["source"], "python-core")
        self.assertEqual(out["node_count"], 22)
        self.assertEqual(out["relationship_count"], 27)
        self.assertEqual(len(out["nodes"]), 22)

    def test_cli_import_and_query(self):
        d = tempfile.mkdtemp()
        db = os.path.join(d, "out.db")
        r = self._run("import", EXAMPLE_CORPUS, "--db", db)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertTrue(out["valid"])
        self.assertEqual(out["nodes_imported"], 22)
        self.assertEqual(out["relationships_imported"], 27)
        # reopen and verify persistence
        repo = KnowledgeRepository(db)
        repo.initialize()
        self.assertEqual(repo.count_nodes(), 22)
        self.assertEqual(repo.count_relationships(), 27)
        self.assertEqual(repo.get_node("python")["type"], "technology")


class PersistenceAfterReopenTests(unittest.TestCase):
    def test_reopen_retains_imported_source(self):
        d = tempfile.mkdtemp()
        db = os.path.join(d, "k.db")
        repo = KnowledgeRepository(db)
        repo.initialize()
        import_source_data(repo, sf.build_source(
            "s", [sf.build_node("a", "concept", "A", "d",
                                relationships=[{"type": "related_to", "target": "b"}]),
                  sf.build_node("b", "concept", "B", "d")]))
        repo.close()
        reopened = KnowledgeRepository(db)
        reopened.initialize()
        self.assertEqual(reopened.count_nodes(), 2)
        self.assertEqual(reopened.count_relationships(), 1)
        self.assertEqual(reopened.get_node("a")["name"], "A")


class IntegrationCorpusTests(unittest.TestCase):
    def setUp(self):
        self.repo = fresh_repo()
        import_source_file(self.repo, EXAMPLE_CORPUS)

    def test_via_repository(self):
        self.assertEqual(self.repo.get_node("python")["type"], "technology")
        followed = self.repo.follow("python", rel_type="related_to")
        self.assertEqual(followed[0][1]["id"], "python-standard-library")
        self.assertEqual(self.repo.count_nodes(), 22)
        self.assertEqual(self.repo.count_relationships(), 27)

    def test_via_knowledge_store(self):
        store = KnowledgeStore(repository=self.repo).load_from_repository()
        self.assertIsNotNone(store.get("python"))
        res = store.search("function")
        self.assertTrue(any(n["id"] == "functions" for _, n in res))
        follow = store.follow("unittest", rel_type="depends_on")
        self.assertEqual(follow[0][1]["id"], "python-standard-library")

    def test_via_knowledge_tools(self):
        store = KnowledgeStore(repository=self.repo).load_from_repository()
        tools = KnowledgeTools(store=store)
        self.assertTrue(tools.get("python")["found"])
        self.assertGreater(tools.search("virtual environment")["count"], 0)
        f = tools.follow("python", rel_type="related_to")
        self.assertIn("python-standard-library",
                      [n["id"] for n in f["nodes"]])
        # provenance surfaced
        self.assertEqual(tools.get("unittest")["provenance"]["source_name"],
                         "python-core")

    def test_relationship_traversal_full_graph(self):
        store = KnowledgeStore(repository=self.repo).load_from_repository()
        tools = KnowledgeTools(store=store)
        # directed traversal from python reaches the standard library
        f = tools.follow("python", max_depth=3)
        ids = {n["id"] for n in f["nodes"]}
        self.assertIn("python-standard-library", ids)
        # directed traversal from unittest reaches the standard library too
        f2 = tools.follow("unittest", max_depth=3)
        ids2 = {n["id"] for n in f2["nodes"]}
        self.assertIn("python-standard-library", ids2)
        # multi-hop: example -> procedure -> concept -> technology
        f3 = tools.follow("simple-function-example", max_depth=4)
        ids3 = {n["id"] for n in f3["nodes"]}
        self.assertIn("python", ids3)
        self.assertIn("define-a-function", ids3)

    def test_type_counts(self):
        counts = {}
        for n in self.repo.get_all_nodes():
            counts[n["type"]] = counts.get(n["type"], 0) + 1
        self.assertEqual(counts["concept"], 10)
        self.assertEqual(counts["procedure"], 3)
        self.assertEqual(counts["rule"], 2)
        self.assertEqual(counts["example"], 4)
        self.assertEqual(counts["dependency"], 1)
        self.assertEqual(counts["technology"], 2)


if __name__ == "__main__":
    unittest.main()
