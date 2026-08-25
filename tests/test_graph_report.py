import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from retrieval.graph_report import build_report, render_text, main
from retrieval.repository import KnowledgeRepository


def _fixture_repo(db_path):
    repo = KnowledgeRepository(db_path)
    repo.initialize()
    s1 = repo.add_source("src-a", version="1.0", location="/k/a")
    s2 = repo.add_source("src-b")
    repo.add_node("n-concept-a", "concept", "A", "desc a", source_id=s1)
    repo.add_node("n-tech-b", "technology", "B", "desc b", source_id=s1)
    repo.add_node("n-entity-c", "entity", "C", "desc c", source_id=s2)
    repo.add_node("n-iso-d", "concept", "D", "isolated d", source_id=s2)
    repo.add_node("n-iso-e", "example", "E", "isolated e", source_id=s2)
    repo.add_relationship("n-concept-a", "uses", "n-tech-b")
    repo.add_relationship("n-concept-a", "related_to", "n-entity-c")
    repo.add_relationship("n-tech-b", "references", "missing-node")
    return repo


def _read_bytes(path):
    with open(path, "rb") as f:
        return f.read()


class GraphReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "k.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_metrics(self):
        repo = _fixture_repo(self.db)
        try:
            r = build_report(self.db)
        finally:
            repo.close()
        self.assertEqual(r["total_nodes"], 5)
        self.assertEqual(r["total_relationships"], 3)
        self.assertEqual(r["total_sources"], 2)
        self.assertEqual(r["isolated_nodes"], 2)
        self.assertEqual(r["isolated_pct"], 40.0)
        self.assertEqual(r["connectivity_pct"], 60.0)
        self.assertEqual(r["dangling_targets"], 1)
        self.assertEqual(
            r["dangling_target_edges"],
            [{"source_node_id": "n-tech-b", "type": "references",
              "target_node_id": "missing-node"}])
        self.assertEqual(
            r["relationships_by_type"],
            {"references": 1, "related_to": 1, "uses": 1})
        self.assertEqual(
            r["nodes_by_type"],
            {"concept": 2, "entity": 1, "example": 1, "technology": 1})
        by_id = {s["source_id"]: s for s in r["nodes_per_source"]}
        self.assertEqual(by_id[1]["node_count"], 2)
        self.assertEqual(by_id[1]["name"], "src-a")
        self.assertEqual(by_id[2]["node_count"], 3)

    def test_report_is_read_only(self):
        repo = _fixture_repo(self.db)
        repo.close()
        before = _read_bytes(self.db)
        build_report(self.db)
        after = _read_bytes(self.db)
        self.assertEqual(before, after)

    def test_deterministic(self):
        repo = _fixture_repo(self.db)
        repo.close()
        self.assertEqual(build_report(self.db), build_report(self.db))

    def test_empty_database(self):
        repo = KnowledgeRepository(self.db)
        repo.initialize()
        repo.close()
        r = build_report(self.db)
        self.assertEqual(r["total_nodes"], 0)
        self.assertEqual(r["total_relationships"], 0)
        self.assertEqual(r["isolated_pct"], 0.0)
        self.assertEqual(r["connectivity_pct"], 0.0)

    def test_render_text_contains_key_facts(self):
        repo = _fixture_repo(self.db)
        repo.close()
        text = render_text(build_report(self.db))
        self.assertIn("nodes:            5", text)
        self.assertIn("dangling targets: 1", text)
        self.assertIn("missing-node", text)

    def test_cli_json_mode(self):
        repo = _fixture_repo(self.db)
        repo.close()
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--db", self.db, "--json"])
        self.assertEqual(code, 0)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed["total_nodes"], 5)

    def test_cli_missing_db_fails_cleanly(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            code = main(["--db", os.path.join(self.tmp.name, "nope.db")])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
