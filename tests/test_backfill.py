import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from knowledge_compiler.backfill import (
    build_report, render_text, main, RULE_E1, RULE_D1)
from retrieval.repository import KnowledgeRepository


def _example_meta(refs):
    return {"evidence_references": [
        {"document": d, "section_path": ["s"], "location": {"path": d,
         "line_start": 1, "line_end": 2}} for d in refs]}


class BackfillFixtureBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "k.db")
        self.repo = KnowledgeRepository(self.db)
        self.repo.initialize()
        s = self.repo.add_source("fixture", version="1.0")
        for nid, name in [("t-glob", "glob"), ("t-json", "json"),
                          ("t-dup-a", "dup"), ("t-dup-b", "dup")]:
            self.repo.add_node(nid, "technology", name, "tech",
                               source_id=s)
        self.repo.add_node("d1", "dependency", "import glob", "dep",
                           source_id=s)
        self.repo.add_node("d2", "dependency", "import json, csv", "dep",
                           source_id=s)
        self.repo.add_node("d3", "dependency", "not an import", "dep",
                           source_id=s)
        self.repo.add_node("e1", "example", "Ex One", "ex", source_id=s,
                           metadata=_example_meta(
                               ["library/glob.rst", "library/glob.rst"]))
        self.repo.add_node("e2", "example", "Ex Two", "ex", source_id=s,
                           metadata=_example_meta(["tutorial/x.rst"]))
        self.repo.add_node("e3", "example", "Ex Three", "ex", source_id=s,
                           metadata=_example_meta(
                               ["library/glob.rst", "library/json.rst"]))
        self.repo.add_node("e4", "example", "Ex Four", "ex", source_id=s,
                           metadata=_example_meta(["library/glob.rst"]))
        self.repo.add_node("e5", "example", "Ex Five", "ex", source_id=s,
                           metadata=_example_meta(["library/dup.rst"]))
        # authored edge that E1 would also propose -> must be classified
        # as duplicate, never re-proposed
        self.repo.add_relationship("e4", "example_of", "t-glob")
        # deliberate dangling target
        self.repo.add_relationship("d3", "references", "ghost-node")

    def tearDown(self):
        self.repo.close()
        self.tmp.cleanup()

    def report(self):
        return build_report(self.db)

    def rules(self):
        return build_report(self.db)["rules"]


class BackfillRuleTests(BackfillFixtureBase):
    def test_e1_proposes_correct_edge_with_label(self):
        r = self.rules()[RULE_E1]
        props = [(p["source_node_id"], p["target_node_id"])
                 for p in r["proposals"]]
        self.assertEqual(props, [("e1", "t-glob")])
        p = r["proposals"][0]
        self.assertEqual(p["label"], "inferred:" + RULE_E1)
        self.assertEqual(p["relationship_type"], "example_of")
        self.assertEqual(p["confidence"], "deterministic")
        self.assertEqual(p["evidence"], {"document": "library/glob.rst",
                                         "module": "glob"})

    def test_e1_duplicate_authored_edge_not_reproposed(self):
        r = self.rules()[RULE_E1]
        self.assertEqual(r["duplicate_edges_already_present"], 1)
        src = {p["source_node_id"] for p in r["duplicate_cases"]}
        self.assertEqual(src, {"e4"})

    def test_e1_unmatched_reasons(self):
        r = self.rules()[RULE_E1]
        self.assertEqual(r["unmatched_nodes"], 2)
        reasons = {u["source_node_id"]: u["reason"]
                   for u in r["unmatched_cases"]}
        self.assertIn("tutorial", reasons["e2"])
        self.assertIn("distinct documents", reasons["e3"])

    def test_e1_ambiguous_target_classification(self):
        r = self.rules()[RULE_E1]
        self.assertEqual(r["ambiguous_matches"], 1)
        case = r["ambiguous_cases"][0]
        self.assertEqual(case["source_node_id"], "e5")
        self.assertEqual(case["candidate_targets"], ["t-dup-a", "t-dup-b"])

    def test_d1_proposes_edges(self):
        r = self.rules()[RULE_D1]
        props = {(p["source_node_id"], p["target_node_id"])
                 for p in r["proposals"]}
        self.assertEqual(props, {("d1", "t-glob"), ("d2", "t-json")})
        for p in r["proposals"]:
            self.assertEqual(p["relationship_type"], "depends_on")
            self.assertTrue(p["label"].startswith("inferred:"))

    def test_d1_partial_module_match_reports_missing_module(self):
        r = self.rules()[RULE_D1]
        unmatched = [u for u in r["unmatched_cases"]
                     if u["source_node_id"] == "d2"]
        self.assertEqual(len(unmatched), 1)
        self.assertIn("csv", unmatched[0]["reason"])

    def test_d1_non_import_name_unmatched(self):
        r = self.rules()[RULE_D1]
        d3 = [u for u in r["unmatched_cases"] if u["source_node_id"] == "d3"]
        self.assertEqual(len(d3), 1)
        self.assertIn("not an import statement", d3[0]["reason"])

    def test_dangling_reported_never_resolved_by_invention(self):
        report = self.report()
        dang = report["dangling_existing"]
        self.assertEqual(len(dang), 1)
        self.assertEqual(dang[0]["target_node_id"], "ghost-node")
        self.assertFalse(dang[0]["resolvable"])

    def test_invalid_target_count_always_zero(self):
        for rid, r in self.rules().items():
            self.assertEqual(r["invalid_targets"], 0, rid)

    def test_proposed_targets_always_exist(self):
        con = self.repo.conn
        ids = {x[0] for x in con.execute("SELECT id FROM nodes")}
        for rid, r in self.rules().items():
            for p in r["proposals"]:
                self.assertIn(p["target_node_id"], ids)


class BackfillProjectionTests(BackfillFixtureBase):
    def test_projected_stats(self):
        ps = self.report()["projected_stats"]
        self.assertEqual(ps["nodes"], 12)
        self.assertEqual(ps["relationships_current"], 2)
        self.assertEqual(ps["isolated_current"], 9)
        self.assertEqual(ps["relationships_projected"], 5)
        self.assertEqual(ps["isolated_projected"], 5)
        self.assertEqual(ps["connectivity_projected_pct"], 58.33)


class BackfillSafetyTests(BackfillFixtureBase):
    def _bytes(self):
        with open(self.db, "rb") as f:
            return f.read()

    def test_read_only_database_file(self):
        before = self._bytes()
        self.report()
        self.assertEqual(before, self._bytes())

    def test_no_json_writes(self):
        # the tool only ever reads SQLite; there is no write API at all
        import knowledge_compiler.backfill as bf
        self.assertFalse(any(hasattr(bf, n) for n in
                             ("write", "save", "commit", "apply")))
        self.assertIsNone(getattr(bf, "open", None) or
                          bf.__dict__.get("__apply__"))

    def test_deterministic_output(self):
        a = json.dumps(self.report(), sort_keys=True)
        b = json.dumps(self.report(), sort_keys=True)
        self.assertEqual(a, b)

    def test_timestamp_free_report(self):
        blob = json.dumps(self.report())
        self.assertNotIn("2026", blob)
        self.assertNotIn("generated_at", blob)


class BackfillIdempotencyTests(BackfillFixtureBase):
    def test_apply_twice_yields_zero_new_edges(self):
        """Simulates the future apply step using ONLY the dry-run output."""
        report = self.report()
        proposals = []
        for rid, r in report["rules"].items():
            proposals.extend(r["proposals"])
        self.assertEqual(len(proposals), 3)

        def apply(edges):
            for p in edges:
                self.repo.add_relationship(
                    p["source_node_id"], p["relationship_type"],
                    p["target_node_id"], label=p.get("label"))

        before_count = self.repo.count_relationships()
        apply(proposals)
        self.assertEqual(self.repo.count_relationships(),
                         before_count + len(proposals))

        # second dry-run against the updated DB: everything is duplicate
        second = build_report(self.db)
        self.assertEqual(sum(r["proposed_new_edges"]
                             for r in second["rules"].values()), 0)
        self.assertEqual(second["duplicates_total"],
                         report["duplicates_total"] + len(proposals))

    def test_unique_key_ignores_label(self):
        import sqlite3
        self.repo.add_relationship("e1", "example_of", "t-glob",
                                   label="inferred:example_library_doc")
        with self.assertRaises(sqlite3.IntegrityError):
            self.repo.add_relationship("e1", "example_of", "t-glob",
                                       label="some other label")

    def test_inferred_labels_distinguishable_from_authored(self):
        self.repo.add_relationship("d1", "depends_on", "t-glob",
                                   label="inferred:" + RULE_D1)
        rows = self.repo.relationships_of("d1")
        inferred = [r for r in rows if (r["label"] or "").startswith(
            "inferred:")]
        authored = [r for r in rows if not (r["label"] or "").startswith(
            "inferred:")]
        self.assertEqual(len(inferred), 1)
        self.assertEqual(authored, [])


class BackfillCLITests(BackfillFixtureBase):
    def test_cli_json(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--db", self.db, "--json"])
        self.assertEqual(code, 0)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(parsed["proposed_edges_total"], 3)
        self.assertIn(RULE_E1, parsed["rules"])
        self.assertIn(RULE_D1, parsed["rules"])

    def test_cli_text_summary(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--db", self.db])
        self.assertEqual(code, 0)
        text = buf.getvalue()
        self.assertIn("Relationship Backfill Dry-Run", text)
        self.assertIn("proposed new edges", text)

    def test_cli_single_rule_filter(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--db", self.db, "--json", "--rule", RULE_D1])
        self.assertEqual(code, 0)
        parsed = json.loads(buf.getvalue())
        self.assertEqual(set(parsed["rules"]), {RULE_D1})

    def test_cli_missing_db_exit_code(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = main(["--db", os.path.join(self.tmp.name, "nope.db")])
        self.assertEqual(code, 2)

    def test_render_text_mentions_projection(self):
        text = render_text(self.report())
        self.assertIn("isolated      : 9 -> 5", text)


if __name__ == "__main__":
    unittest.main()
