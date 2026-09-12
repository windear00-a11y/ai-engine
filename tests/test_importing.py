"""Tests for the Knowledge Import Verification Layer (Safe Import Dry-Run v1).

Covers: valid plans, invalid nodes, invalid relationships, duplicate
identities, missing provenance, dangling relationships, repository constraint
violations, atomic rollback, projected counts, deterministic output, read-only
guarantee, temporary repository isolation, and provenance retention.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from importing.plan_loader import load_plan, load_plan_data, PlanLoadError
from importing.verifier import verify_plan
from importing.dry_run import dry_run, apply_plan, source_identity
from importing.report import build_report, human_summary, to_json
from retrieval.repository import KnowledgeRepository

KNOWLEDGE_DB = os.path.join(_ROOT, "database", "knowledge.db")


# -- helpers ---------------------------------------------------------------

def prov(cid="cand-1"):
    return [{
        "candidate_id": cid,
        "document": "doc.rst",
        "section_path": ["Section"],
        "location": {"path": "doc.rst", "line_start": 1, "line_end": 1},
        "evidence": f"evidence for {cid}",
    }]


def node(nid, ntype="concept", name=None, description=None, provenance=None):
    return {
        "id": nid,
        "type": ntype,
        "name": name or nid,
        "description": description or f"description of {nid}",
        "provenance": provenance if provenance is not None else prov(f"c-{nid}"),
    }


def rel(src, rtype, tgt, label=None):
    r = {
        "source_node_id": src,
        "relationship_type": rtype,
        "target_node_id": tgt,
        "label": label or f"{src} {rtype} {tgt}",
        "target_name": tgt,
        "provenance_identity": "id-1",
    }
    return r


def decision(cid, state="HOLD", reason_code="HOLD:identity:duplicate_identity_conflict"):
    return {
        "candidate_id": cid, "candidate_kind": "api_declaration",
        "decision": state, "reason_code": reason_code, "confidence": "high",
        "identity": "id", "summary": "x", "document": "d.rst",
        "provenance": {}, "reason": "x", "evidence": "x",
    }


def build_plan(nodes, rels, decisions=None, source="/tmp/candidate-output",
               guard=True, version=None):
    node_types = dict(Counter(n["type"] for n in nodes))
    rel_types = dict(Counter(r["relationship_type"] for r in rels))
    node_ids = {n["id"] for n in nodes}
    dangling = sum(1 for r in rels if r["target_node_id"] not in node_ids)
    plan = {
        "source": source,
        "read_only_guard": {
            "sqlite_writes_forbidden": guard,
            "note": "preview only -- no database was written",
        },
        "preview": {
            "proposed_nodes": nodes,
            "proposed_nodes_count": len(nodes),
            "proposed_nodes_by_type": node_types,
            "proposed_relationships": rels,
            "proposed_relationships_count": len(rels),
            "proposed_relationships_by_type": rel_types,
            "reference_integrity": {
                "dangling_relationship_targets": dangling,
            },
            "summary": {},
        },
        "decisions": decisions or [],
        "identities": [],
    }
    if version is not None:
        plan["version"] = version
    return plan


def write_plan(tmp, plan, name="import_plan.json"):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(plan, f)
    return path


def valid_plan():
    return build_plan(
        [node("a", "concept"), node("b", "technology")],
        [rel("a", "related_to", "b")],
    )


# -- loader ----------------------------------------------------------------

class PlanLoaderTests(unittest.TestCase):
    def test_loads_valid_plan_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_plan(tmp, valid_plan())
            plan = load_plan(path)
            self.assertEqual(plan["preview"]["proposed_nodes_count"], 2)

    def test_missing_file_raises(self):
        with self.assertRaises(PlanLoadError):
            load_plan("/nonexistent/import_plan.json")

    def test_invalid_json_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w") as f:
                f.write("{not json")
            with self.assertRaises(PlanLoadError):
                load_plan(path)

    def test_non_dict_plan_raises(self):
        with self.assertRaises(PlanLoadError):
            load_plan_data([1, 2, 3])


# -- verifier --------------------------------------------------------------

class VerifierTests(unittest.TestCase):
    def test_valid_plan_is_valid(self):
        result = verify_plan(valid_plan())
        self.assertTrue(result.valid)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.nodes), 2)

    def test_dangling_target_is_allowed_as_warning(self):
        plan = build_plan([node("a")], [rel("a", "related_to", "MISSING")])
        result = verify_plan(plan)
        self.assertTrue(result.valid)
        self.assertEqual(len(result.dangling_targets), 1)
        codes = [w.code for w in result.warnings]
        self.assertIn("relationship_target_dangling", codes)

    def test_invalid_node_structure(self):
        plan = valid_plan()
        plan["preview"]["proposed_nodes"][0] = "not-a-node"
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        self.assertIn("malformed_node", [e.code for e in result.errors])

    def test_missing_node_id(self):
        plan = valid_plan()
        plan["preview"]["proposed_nodes"][0]["id"] = ""
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        self.assertIn("node_id", [e.code for e in result.errors])

    def test_invalid_node_type(self):
        plan = valid_plan()
        plan["preview"]["proposed_nodes"][0]["type"] = ""
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        self.assertIn("node_type", [e.code for e in result.errors])

    def test_duplicate_node_id_is_error(self):
        plan = build_plan([node("a"), node("a")], [])
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        self.assertIn("duplicate_node_id", [e.code for e in result.errors])

    def test_missing_provenance_is_error(self):
        plan = valid_plan()
        plan["preview"]["proposed_nodes"][0]["provenance"] = []
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        self.assertIn("missing_provenance", [e.code for e in result.errors])

    def test_malformed_provenance_metadata(self):
        plan = valid_plan()
        plan["preview"]["proposed_nodes"][0]["provenance"] = [{"candidate_id": 3}]
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        codes = [e.code for e in result.errors]
        self.assertIn("evidence_reference", codes)

    def test_unsafe_absolute_path_is_error(self):
        plan = valid_plan()
        plan["preview"]["proposed_nodes"][0]["provenance"][0]["location"] = {
            "path": "/etc/passwd", "line_start": 1, "line_end": 1}
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        self.assertIn("unsafe_path", [e.code for e in result.errors])

    def test_guard_off_is_error(self):
        plan = build_plan([node("a")], [], guard=False)
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        self.assertIn("guard_off", [e.code for e in result.errors])

    def test_invalid_relationship_structure(self):
        plan = valid_plan()
        plan["preview"]["proposed_relationships"][0]["source_node_id"] = ""
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        self.assertIn("relationship_structure", [e.code for e in result.errors])

    def test_missing_relationship_source_is_error(self):
        plan = build_plan([node("a")], [rel("GHOST", "extends", "a")])
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        self.assertIn("relationship_source_missing",
                      [e.code for e in result.errors])
        self.assertEqual(len(result.unresolved_sources), 1)

    def test_duplicate_relationship_is_error(self):
        plan = build_plan([node("a"), node("b")],
                          [rel("a", "related_to", "b"), rel("a", "related_to", "b")])
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        self.assertIn("duplicate_relationship", [e.code for e in result.errors])

    def test_count_mismatch_is_error(self):
        plan = valid_plan()
        plan["preview"]["proposed_nodes_count"] = 99
        result = verify_plan(plan)
        self.assertFalse(result.valid)
        self.assertIn("count_mismatch", [e.code for e in result.errors])


# -- dry run ---------------------------------------------------------------

class DryRunTests(unittest.TestCase):
    def test_valid_plan_is_safe_with_projected_counts(self):
        plan = build_plan(
            [node("a", "concept"), node("b", "technology"), node("c", "dependency")],
            [rel("a", "related_to", "b")],
        )
        r = dry_run(plan)
        self.assertTrue(r.safe)
        self.assertTrue(r.verified)
        self.assertFalse(r.rollback)
        self.assertEqual(r.sources_inserted, 1)
        self.assertEqual(r.nodes_inserted, 3)
        self.assertEqual(r.relationships_inserted, 1)
        self.assertEqual(r.node_types,
                         {"concept": 1, "technology": 1, "dependency": 1})
        self.assertEqual(r.relationship_types, {"related_to": 1})

    def test_dangling_relationship_is_safe_but_warned(self):
        plan = build_plan([node("a")], [rel("a", "uses", "MISSING")])
        r = dry_run(plan)
        self.assertTrue(r.safe)
        self.assertEqual(len(r.dangling_targets), 1)
        self.assertIn("relationship_target_dangling",
                      [w["code"] for w in r.warnings])

    def test_missing_source_repository_constraint_violation(self):
        plan = build_plan([node("a")], [rel("GHOST", "extends", "a")])
        r = dry_run(plan)
        self.assertFalse(r.safe)
        self.assertTrue(r.rollback)
        self.assertEqual(r.nodes_inserted, 0)
        self.assertEqual(r.sources_inserted, 0)
        codes = [e["code"] for e in r.errors]
        self.assertIn("relationship_source_missing", codes)
        self.assertIn("simulation_failed", codes)
        self.assertIn("FOREIGN KEY", r.errors[0]["message"])

    def test_atomic_rollback_on_duplicate_node_id(self):
        plan = build_plan([node("a"), node("a")], [])
        r = dry_run(plan)
        self.assertFalse(r.safe)
        self.assertTrue(r.rollback)
        self.assertEqual(r.nodes_inserted, 0)
        self.assertEqual(r.relationships_inserted, 0)
        self.assertEqual(r.sources_inserted, 0)
        self.assertIn("simulation_failed", [e["code"] for e in r.errors])
        self.assertIn("UNIQUE constraint", r.errors[0]["message"])

    def test_atomic_rollback_leaves_zero_rows_in_repo(self):
        # Direct proof against the repository the simulation used.
        plan = build_plan([node("a"), node("b"), node("c")], [])
        plan["preview"]["proposed_nodes"][2] = dict(node("a"))  # duplicate
        verification = verify_plan(plan)
        repo = KnowledgeRepository(":memory:")
        repo.initialize()
        ok, counts, failing, reason = apply_plan(repo, plan, verification)
        self.assertFalse(ok)
        self.assertEqual(repo.conn.execute(
            "SELECT COUNT(*) FROM sources").fetchone()[0], 0)
        self.assertEqual(repo.conn.execute(
            "SELECT COUNT(*) FROM nodes").fetchone()[0], 0)
        self.assertEqual(repo.conn.execute(
            "SELECT COUNT(*) FROM relationships").fetchone()[0], 0)
        repo.close()

    def test_provenance_retention_in_stored_nodes(self):
        plan = build_plan([node("a", "concept"), node("b", "technology")],
                          [rel("a", "related_to", "b")], version="1.2")
        verification = verify_plan(plan)
        repo = KnowledgeRepository(":memory:")
        repo.initialize()
        ok, counts, _, _ = apply_plan(repo, plan, verification)
        self.assertTrue(ok)
        self.assertEqual(counts["nodes"], 2)
        stored = repo.get_node("a")
        # source provenance (source_id, name, version, location, imported_at)
        prov_ = stored["provenance"]
        self.assertIn("source_id", prov_)
        self.assertEqual(prov_["source_name"], "import-plan:candidate-output")
        self.assertEqual(prov_["source_version"], "1.2")
        self.assertEqual(prov_["source_location"], "/tmp/candidate-output")
        self.assertIn("imported_at", prov_)
        # evidence references retained as node metadata
        self.assertEqual(stored["evidence_reference_count"], 1)
        self.assertEqual(stored["evidence_references"][0]["candidate_id"], "c-a")
        # relationships retained
        self.assertEqual(stored["relationships"][0]["type"], "related_to")
        repo.close()

    def test_projected_counts_in_report(self):
        plan = build_plan(
            [node("a", "concept"), node("b", "technology"),
             node("c", "concept"), node("d", "entity")],
            [rel("a", "related_to", "b"), rel("a", "extends", "d")],
        )
        r = dry_run(plan)
        report = build_report(r.verification, r)
        pc = report["projected_counts"]
        self.assertEqual(pc["sources"], 1)
        self.assertEqual(pc["nodes"], 4)
        self.assertEqual(pc["nodes_by_type"],
                         {"concept": 2, "technology": 1, "entity": 1})
        self.assertEqual(pc["relationships"], 2)
        self.assertEqual(pc["relationships_by_type"],
                         {"related_to": 1, "extends": 1})

    def test_deterministic_output(self):
        plan = valid_plan()
        r1 = dry_run(plan)
        r2 = dry_run(plan)
        self.assertEqual(to_json(build_report(r1.verification, r1)),
                         to_json(build_report(r2.verification, r2)))

    def test_held_and_rejected_records_reported(self):
        plan = build_plan([node("a")], [],
                          decisions=[decision("h1"), decision("r1", "REJECT",
                                    "REJECT:code_example:shell_or_text_transcript")])
        r = dry_run(plan)
        self.assertEqual(r.skipped_held,
                         {"held": 1, "rejected": 1, "total": 2})
        self.assertEqual(r.conflicts, {"identity_conflicted_records": 1})

    def test_source_identity_is_deterministic(self):
        plan = valid_plan()
        self.assertEqual(source_identity(plan),
                         source_identity(dict(plan)))
        self.assertEqual(source_identity(plan)[0], "import-plan:candidate-output")


# -- report ----------------------------------------------------------------

class ReportTests(unittest.TestCase):
    REQUIRED_KEYS = {
        "safe", "sources", "nodes", "relationships", "conflicts", "errors",
        "warnings", "provenance_coverage", "projected_counts",
    }

    def test_dry_run_report_has_required_shape(self):
        r = dry_run(valid_plan())
        report = build_report(r.verification, r)
        self.assertLessEqual(self.REQUIRED_KEYS, set(report))
        self.assertTrue(report["safe"])
        self.assertEqual(report["sources"][0]["node_count"], 2)
        self.assertIn("by_type", report["nodes"])
        self.assertIn("dangling_targets", report["relationships"])
        self.assertIn("coverage", report["provenance_coverage"])
        self.assertEqual(report["projected_counts"]["nodes"], 2)

    def test_verify_report_shape(self):
        report = build_report(verify_plan(valid_plan()))
        self.assertLessEqual(self.REQUIRED_KEYS, set(report))
        self.assertTrue(report["safe"])
        self.assertIsNone(report["rollback"])
        self.assertFalse(report["simulated"])

    def test_human_summary_renders(self):
        r = dry_run(valid_plan())
        report = build_report(r.verification, r)
        text = human_summary(report)
        self.assertIn("SAFE: YES", text)
        self.assertIn("Projected counts:", text)
        self.assertIn("Provenance coverage:", text)


# -- read-only guarantees --------------------------------------------------

class ReadOnlyTests(unittest.TestCase):
    def test_dry_run_never_touches_production_db(self):
        existed = os.path.exists(KNOWLEDGE_DB)
        r = dry_run(valid_plan())
        self.assertTrue(r.safe)
        self.assertEqual(os.path.exists(KNOWLEDGE_DB), existed)

    def test_dry_run_creates_no_database_files(self):
        plan = valid_plan()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                dry_run(plan)
                found = [p for _, _, fs in os.walk(tmp)
                         for p in fs if p.endswith(".db") or p.endswith(".sqlite")]
                self.assertEqual(found, [])
            finally:
                os.chdir(_ROOT)

    def test_temporary_repository_isolation(self):
        # Two independent dry runs must not share state.
        a = dry_run(valid_plan())
        b = dry_run(valid_plan())
        self.assertEqual(a.nodes_inserted, b.nodes_inserted)
        self.assertEqual(build_report(a.verification, a)["projected_counts"],
                         build_report(b.verification, b)["projected_counts"])


# -- CLI -------------------------------------------------------------------

class ImportingCLITests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, "-m", "importing", *args],
                              cwd=_ROOT, capture_output=True, text=True)

    @staticmethod
    def _extract_json(stdout):
        # The CLI prints the JSON report first, then the human summary.
        end = stdout.find("\n}\n")
        if end == -1:
            return json.loads(stdout)
        return json.loads(stdout[:end + 3])

    def test_verify_command_prints_json_then_human(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_plan(tmp, valid_plan())
            r = self._run("verify", path)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = self._extract_json(r.stdout)
            self.assertTrue(out["safe"])
            self.assertIn("SAFE: YES", r.stdout)

    def test_dry_run_command_prints_json_then_human(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_plan(tmp, valid_plan())
            r = self._run("dry-run", path)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = self._extract_json(r.stdout)
            self.assertTrue(out["safe"])
            self.assertEqual(out["projected_counts"]["nodes"], 2)

    def test_verify_invalid_plan_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = build_plan([node("a", "")], [])
            path = write_plan(tmp, plan)
            r = self._run("verify", path)
            self.assertEqual(r.returncode, 1)
            out = self._extract_json(r.stdout)
            self.assertFalse(out["safe"])
            self.assertIn("node_type", [e["code"] for e in out["errors"]])

    def test_missing_plan_returns_error_json(self):
        r = self._run("verify", "/nonexistent/plan.json")
        self.assertEqual(r.returncode, 1)
        out = json.loads(r.stdout)
        self.assertIn("error", out)

    def test_cli_commands_create_no_database_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_plan(tmp, valid_plan())
            os.chdir(tmp)
            try:
                self._run("verify", path)
                self._run("dry-run", path)
                found = [p for _, _, fs in os.walk(tmp)
                         for p in fs if p.endswith(".db") or p.endswith(".sqlite")]
                self.assertEqual(found, [])
            finally:
                os.chdir(_ROOT)


if __name__ == "__main__":
    unittest.main()
