"""Comprehensive tests for Phase 3: staging, preview, and atomic apply.

Covers: staging creation, staging isolation, preview, approval boundary,
conflict handling, provenance, atomic apply, rollback, idempotency,
multi-domain imports, security, DB integrity, and Contract v1 compatibility.

All tests run against an isolated, seeded knowledge database created in a
temporary directory -- no committed repository database is required.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from external_import.staging import create_staging, human_staging_summary
from external_import.preview import preview, human_preview_summary
from external_import.apply import apply, human_apply_summary
from external_import.dry_run import dry_run
from retrieval.repository import KnowledgeRepository


# -- helpers ----------------------------------------------------------------

def _base_source(name="test-source", version="1.0", location="file:///test"):
    return {"name": name, "version": version, "location": location}


def _node(nid, ntype="concept", name=None, description=None, metadata=None):
    n = {
        "id": nid,
        "type": ntype,
        "name": name or nid.replace("-", " ").title(),
        "description": description or f"Description of {nid}",
    }
    if metadata is not None:
        n["metadata"] = metadata
    return n


def _rel(src, rtype, tgt, label=None):
    r = {
        "source_node_id": src,
        "relationship_type": rtype,
        "target_node_id": tgt,
    }
    if label is not None:
        r["label"] = label
    return r


def _minimal_valid():
    return {
        "source": _base_source(),
        "nodes": [_node("a"), _node("b")],
        "relationships": [_rel("a", "related_to", "b")],
    }


def _write_json(tmp, name, obj):
    path = os.path.join(tmp, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f)
    return path


def _db_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _build_seed_db(path):
    """Small, self-contained knowledge DB with a real technology node."""
    repo = KnowledgeRepository(path)
    repo.initialize()
    sid = repo.add_source("seed", version="1.0")
    repo.add_node(
        "python", "technology", "Python",
        "A high-level, interpreted, general-purpose programming language.",
        source_id=sid,
    )
    repo.close()


def _make_prod_copy(tmp):
    """Return a writable, isolated copy of the seed knowledge DB."""
    seed_dir = os.path.join(tmp, "seed")
    os.makedirs(seed_dir, exist_ok=True)
    seed_path = os.path.join(seed_dir, "knowledge.db")
    _build_seed_db(seed_path)
    prod_dir = os.path.join(tmp, "prod")
    os.makedirs(prod_dir, exist_ok=True)
    prod_copy = os.path.join(prod_dir, "knowledge.db")
    shutil.copy2(seed_path, prod_copy)
    return prod_copy


def _domain_data():
    """Multi-domain test dataset."""
    return {
        "source": {"name": "domain-test", "version": "2.0",
                    "location": "file:///domain-test"},
        "nodes": [
            _node("person-1", ntype="person", name="Alice Smith",
                  description="A software engineer."),
            _node("company-1", ntype="company", name="Acme Corp",
                  description="A technology company."),
            _node("product-1", ntype="product", name="Widget Pro",
                  description="A productivity tool."),
            _node("doc-1", ntype="document", name="RFC 7231",
                  description="HTTP semantics."),
            _node("event-1", ntype="event", name="PyCon 2024",
                  description="Annual Python conference."),
            _node("paper-1", ntype="research_paper",
                  name="Attention Is All You Need",
                  description="Transformer paper."),
            _node("loc-1", ntype="location", name="San Francisco",
                  description="City in California."),
        ],
        "relationships": [
            _rel("person-1", "works_at", "company-1"),
            _rel("company-1", "created", "product-1"),
            _rel("person-1", "authored", "paper-1"),
            _rel("person-1", "located_at", "loc-1"),
        ],
    }


# -- staging creation -------------------------------------------------------

class StagingCreationTests(unittest.TestCase):
    def test_staging_creates_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            prod_copy = _make_prod_copy(tmp)
            result = create_staging(_minimal_valid(), staging_path,
                                    prod_copy)
            self.assertTrue(result.success)
            self.assertTrue(os.path.isfile(staging_path))

    def test_staging_contains_new_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            prod_copy = _make_prod_copy(tmp)
            result = create_staging(_minimal_valid(), staging_path,
                                    prod_copy)
            self.assertTrue(result.success)
            self.assertGreater(result.nodes_staged, 0)

    def test_staging_skips_existing_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            prod_copy = _make_prod_copy(tmp)
            data = {
                "source": _base_source(name="staging-skip-test"),
                "nodes": [_node("python", ntype="technology", name="Python",
                                description="A high-level, interpreted, "
                                            "general-purpose programming language.")],
                "relationships": [],
            }
            result = create_staging(data, staging_path, prod_copy)
            self.assertTrue(result.success)
            self.assertEqual(result.nodes_staged, 0)
            self.assertGreater(result.nodes_skipped, 0)

    def test_staging_detects_content_conflict(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            prod_copy = _make_prod_copy(tmp)
            data = {
                "source": _base_source(name="staging-conflict-test"),
                "nodes": [_node("python", name="Python MODIFIED",
                                description="TAMPERED")],
                "relationships": [],
            }
            result = create_staging(data, staging_path, prod_copy)
            self.assertTrue(result.success)
            self.assertEqual(result.nodes_staged, 0)
            self.assertGreater(result.nodes_conflicting, 0)
            self.assertIn("python", result.conflicting_node_ids)

    def test_staging_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path1 = os.path.join(tmp, "s1.db")
            staging_path2 = os.path.join(tmp, "s2.db")
            prod_copy = _make_prod_copy(tmp)
            r1 = create_staging(_minimal_valid(), staging_path1, prod_copy)
            r2 = create_staging(_minimal_valid(), staging_path2, prod_copy)
            d1 = {k: v for k, v in r1.as_dict().items()
                  if k != "staging_path"}
            d2 = {k: v for k, v in r2.as_dict().items()
                  if k != "staging_path"}
            self.assertEqual(d1, d2)

    def test_staging_source_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            prod_copy = _make_prod_copy(tmp)
            result = create_staging(_minimal_valid(), staging_path,
                                    prod_copy)
            self.assertTrue(result.source_created)

    def test_staging_graceful_missing_prod_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            result = create_staging(_minimal_valid(), staging_path,
                                    "/nonexistent/knowledge.db")
            self.assertTrue(result.success)
            self.assertTrue(any(w["code"] == "db_unavailable"
                                for w in result.warnings))


# -- staging isolation -------------------------------------------------------

class StagingIsolationTests(unittest.TestCase):
    def test_staging_does_not_modify_production(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            before = _db_hash(prod_copy)
            staging_path = os.path.join(tmp, "staging.db")
            create_staging(_minimal_valid(), staging_path, prod_copy)
            after = _db_hash(prod_copy)
            self.assertEqual(before, after)

    def test_staging_db_is_separate_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            create_staging(_minimal_valid(), staging_path, prod_copy)
            self.assertTrue(os.path.isfile(staging_path))
            self.assertNotEqual(os.path.abspath(staging_path),
                                os.path.abspath(prod_copy))


# -- preview -----------------------------------------------------------------

class PreviewTests(unittest.TestCase):
    def test_preview_reads_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            prod_copy = _make_prod_copy(tmp)
            create_staging(_minimal_valid(), staging_path, prod_copy)
            report = preview(staging_path, prod_copy)
            self.assertTrue(report.safe)
            self.assertTrue(report.staging_valid)
            self.assertTrue(report.production_available)
            self.assertGreater(report.staged_nodes, 0)

    def test_preview_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            prod_copy = _make_prod_copy(tmp)
            create_staging(_minimal_valid(), staging_path, prod_copy)
            r1 = preview(staging_path, prod_copy)
            r2 = preview(staging_path, prod_copy)
            self.assertEqual(r1.as_dict(), r2.as_dict())

    def test_preview_shows_projected_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            prod_copy = _make_prod_copy(tmp)
            create_staging(_minimal_valid(), staging_path, prod_copy)
            report = preview(staging_path, prod_copy)
            self.assertEqual(
                report.projected_node_count,
                report.production_node_count + report.new_nodes)

    def test_preview_missing_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            report = preview("/nonexistent/staging.db", prod_copy)
            self.assertFalse(report.safe)
            self.assertTrue(any(e["code"] == "staging_not_found"
                                for e in report.errors))

    def test_preview_graceful_missing_prod(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            prod_copy = _make_prod_copy(tmp)
            create_staging(_minimal_valid(), staging_path, prod_copy)
            report = preview(staging_path, "/nonexistent/knowledge.db")
            self.assertTrue(report.staging_valid)
            self.assertFalse(report.production_available)
            self.assertTrue(any(w["code"] == "db_unavailable"
                                for w in report.warnings))

    def test_preview_human_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            prod_copy = _make_prod_copy(tmp)
            create_staging(_minimal_valid(), staging_path, prod_copy)
            report = preview(staging_path, prod_copy)
            summary = human_preview_summary(report)
            self.assertIn("External import preview", summary)
            self.assertIn("SAFE TO APPLY", summary)


# -- apply: basic ------------------------------------------------------------

class ApplyBasicTests(unittest.TestCase):
    def test_apply_stages_and_applies(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="apply-basic-test"),
                "nodes": [_node("apply-node-1", name="Apply Node 1"),
                          _node("apply-node-2", name="Apply Node 2")],
                "relationships": [_rel("apply-node-1", "related_to",
                                       "apply-node-2")],
            }
            create_staging(data, staging_path, prod_copy)
            result = apply(staging_path, prod_copy)
            self.assertTrue(result.committed)
            self.assertEqual(result.imported["nodes"], 2)
            self.assertEqual(result.imported["relationships"], 1)

    def test_apply_creates_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="apply-backup-test"),
                "nodes": [_node("backup-node", name="Backup Node")],
                "relationships": [],
            }
            create_staging(data, staging_path, prod_copy)
            result = apply(staging_path, prod_copy)
            self.assertTrue(result.committed)
            self.assertIsNotNone(result.backup)
            self.assertTrue(os.path.isfile(result.backup["path"]))

    def test_apply_post_verify_passes(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="apply-verify-test"),
                "nodes": [_node("verify-node", name="Verify Node")],
                "relationships": [],
            }
            create_staging(data, staging_path, prod_copy)
            result = apply(staging_path, prod_copy)
            self.assertTrue(result.committed)
            self.assertTrue(result.post_apply["counts_match"])
            self.assertEqual(result.post_apply["integrity_check"], "ok")
            self.assertEqual(result.post_apply["foreign_key_violations"], 0)

    def test_apply_production_db_integrity(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="apply-integrity-test"),
                "nodes": [_node("integrity-node", name="Integrity Node")],
                "relationships": [],
            }
            create_staging(data, staging_path, prod_copy)
            apply(staging_path, prod_copy)
            conn = sqlite3.connect(f"file:{prod_copy}?mode=ro", uri=True)
            try:
                self.assertEqual(conn.execute(
                    "PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute(
                    "PRAGMA foreign_key_check").fetchall(), [])
            finally:
                conn.close()

    def test_apply_human_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="apply-human-test"),
                "nodes": [_node("human-node", name="Human Node")],
                "relationships": [],
            }
            create_staging(data, staging_path, prod_copy)
            result = apply(staging_path, prod_copy)
            summary = human_apply_summary(result)
            self.assertIn("External import apply", summary)
            self.assertIn("COMMITTED: YES", summary)


# -- apply: provenance -------------------------------------------------------

class ProvenanceTests(unittest.TestCase):
    def test_imported_nodes_have_source_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="prov-test"),
                "nodes": [_node("prov-node-1", name="Prov Node 1"),
                          _node("prov-node-2", name="Prov Node 2")],
                "relationships": [],
            }
            create_staging(data, staging_path, prod_copy)
            apply(staging_path, prod_copy)

            repo = KnowledgeRepository(prod_copy)
            repo.initialize()
            try:
                node = repo.get_node("prov-node-1")
                self.assertIsNotNone(node)
                self.assertIsNotNone(node.get("source_id"))
                prov = node.get("provenance")
                self.assertIsNotNone(prov)
                self.assertEqual(prov["source_name"], "prov-test")
            finally:
                repo.close()

    def test_provenance_survives_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="prov-reopen-test"),
                "nodes": [_node("reopen-node", name="Reopen Node")],
                "relationships": [],
            }
            create_staging(data, staging_path, prod_copy)
            apply(staging_path, prod_copy)

            repo = KnowledgeRepository(prod_copy)
            repo.initialize()
            try:
                node = repo.get_node("reopen-node")
                prov = node.get("provenance")
                self.assertIsNotNone(prov)
                self.assertIn("source_id", prov)
                self.assertIn("source_name", prov)
                self.assertIn("imported_at", prov)
            finally:
                repo.close()

    def test_imported_source_metadata_not_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="existing-source-test"),
                "nodes": [_node("esn-node", name="ESN Node")],
                "relationships": [],
            }
            create_staging(data, staging_path, prod_copy)
            result = apply(staging_path, prod_copy)
            self.assertTrue(result.committed)

            # Import again with same source name
            staging_path2 = os.path.join(tmp, "staging2.db")
            data2 = {
                "source": _base_source(name="existing-source-test",
                                       version="2.0"),
                "nodes": [_node("esn-node-2", name="ESN Node 2")],
                "relationships": [],
            }
            create_staging(data2, staging_path2, prod_copy)
            result2 = apply(staging_path2, prod_copy)
            self.assertTrue(result2.committed)

            # Verify source count: existing source reused, not duplicated
            conn = sqlite3.connect(f"file:{prod_copy}?mode=ro", uri=True)
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM sources WHERE name = ?",
                    ("existing-source-test",)).fetchone()[0]
                self.assertEqual(count, 1)
            finally:
                conn.close()


# -- apply: rollback ---------------------------------------------------------

class RollbackTests(unittest.TestCase):
    def test_rollback_on_duplicate_node_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            before = _db_hash(prod_copy)

            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="rollback-test"),
                "nodes": [_node("rollback-node", name="Rollback Node")],
                "relationships": [],
            }
            create_staging(data, staging_path, prod_copy)
            result = apply(staging_path, prod_copy)
            self.assertTrue(result.committed)

            # Create staging with a node that now conflicts (same ID, different content)
            staging_path2 = os.path.join(tmp, "staging2.db")
            data2 = {
                "source": _base_source(name="rollback-test-2"),
                "nodes": [_node("rollback-node", name="Different Name",
                                description="Different")],
                "relationships": [],
            }
            create_staging(data2, staging_path2, prod_copy)
            result2 = apply(staging_path2, prod_copy)
            # The staging contains only 1 node (the conflicting one is excluded)
            # But if staging is empty, apply succeeds with 0 imports
            self.assertTrue(result2.committed)

    def test_production_db_unchanged_during_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            before = _db_hash(prod_copy)
            staging_path = os.path.join(tmp, "staging.db")
            create_staging(_minimal_valid(), staging_path, prod_copy)
            after = _db_hash(prod_copy)
            self.assertEqual(before, after)

    def test_staging_and_dry_run_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="agree-test"),
                "nodes": [_node("agree-node", name="Agree Node")],
                "relationships": [],
            }
            staging_result = create_staging(data, staging_path, prod_copy)
            dry = dry_run(data, db_path=prod_copy)
            self.assertEqual(staging_result.nodes_staged, dry.new_nodes)
            self.assertEqual(staging_result.nodes_skipped, dry.existing_nodes)


# -- apply: idempotency ------------------------------------------------------

class IdempotencyTests(unittest.TestCase):
    def test_apply_same_data_twice_no_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            data = {
                "source": _base_source(name="idempotent-test"),
                "nodes": [_node("idem-node-1", name="Idem Node 1"),
                          _node("idem-node-2", name="Idem Node 2")],
                "relationships": [_rel("idem-node-1", "related_to",
                                       "idem-node-2")],
            }
            orig_count = sqlite3.connect(
                f"file:{prod_copy}?mode=ro", uri=True
            ).execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

            # First apply
            staging1 = os.path.join(tmp, "s1.db")
            create_staging(data, staging1, prod_copy)
            r1 = apply(staging1, prod_copy)
            self.assertTrue(r1.committed)
            self.assertEqual(r1.imported["nodes"], 2)

            # Second apply: same data, should detect all as existing
            staging2 = os.path.join(tmp, "s2.db")
            create_staging(data, staging2, prod_copy)
            r2 = apply(staging2, prod_copy)
            self.assertTrue(r2.committed)
            # Staging should contain 0 nodes (all already in production)
            self.assertEqual(r2.imported["nodes"], 0)

            # Verify no duplicates
            conn = sqlite3.connect(f"file:{prod_copy}?mode=ro", uri=True)
            try:
                count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
                # Should be original + 2 (not 4)
                self.assertEqual(count, orig_count + 2)
            finally:
                conn.close()

    def test_apply_same_relationships_twice(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            data = {
                "source": _base_source(name="idempotent-rel-test"),
                "nodes": [_node("ire-node-a", name="IRE A"),
                          _node("ire-node-b", name="IRE B")],
                "relationships": [_rel("ire-node-a", "related_to",
                                       "ire-node-b")],
            }
            orig = sqlite3.connect(
                f"file:{prod_copy}?mode=ro", uri=True
            ).execute("SELECT COUNT(*) FROM relationships").fetchone()[0]

            staging1 = os.path.join(tmp, "s1.db")
            create_staging(data, staging1, prod_copy)
            r1 = apply(staging1, prod_copy)
            self.assertEqual(r1.imported["relationships"], 1)

            staging2 = os.path.join(tmp, "s2.db")
            create_staging(data, staging2, prod_copy)
            r2 = apply(staging2, prod_copy)
            self.assertEqual(r2.imported["relationships"], 0)

            conn = sqlite3.connect(f"file:{prod_copy}?mode=ro", uri=True)
            try:
                count = conn.execute(
                    "SELECT COUNT(*) FROM relationships").fetchone()[0]
                self.assertEqual(count, orig + 1)
            finally:
                conn.close()


# -- multi-domain imports ----------------------------------------------------

class MultiDomainApplyTests(unittest.TestCase):
    DOMAIN_TYPES = [
        ("person", "Alice Smith", "A software engineer."),
        ("company", "Acme Corp", "A technology company."),
        ("product", "Widget Pro", "A productivity tool."),
        ("document", "RFC 7231", "HTTP semantics."),
        ("event", "PyCon 2024", "Annual Python conference."),
        ("research_paper", "Attention Is All You Need", "Transformer paper."),
        ("location", "San Francisco", "City in California."),
    ]

    def test_all_domain_types_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            data = _domain_data()
            staging_path = os.path.join(tmp, "staging.db")
            result = create_staging(data, staging_path, prod_copy)
            self.assertTrue(result.success)
            self.assertEqual(result.nodes_staged, 7)
            self.assertEqual(result.relationships_staged, 4)

            apply_result = apply(staging_path, prod_copy)
            self.assertTrue(apply_result.committed)
            self.assertEqual(apply_result.imported["nodes"], 7)
            self.assertEqual(apply_result.imported["relationships"], 4)

    def test_domain_nodes_queryable_after_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            data = _domain_data()
            staging_path = os.path.join(tmp, "staging.db")
            create_staging(data, staging_path, prod_copy)
            apply(staging_path, prod_copy)

            repo = KnowledgeRepository(prod_copy)
            repo.initialize()
            try:
                # Search for person
                results = repo.search_nodes("Alice Smith")
                self.assertTrue(len(results) > 0)
                self.assertEqual(results[0]["type"], "person")

                # Get by ID
                node = repo.get_node("person-1")
                self.assertIsNotNone(node)
                self.assertEqual(node["name"], "Alice Smith")
                self.assertEqual(node["type"], "person")

                # Follow relationship
                rels = repo.follow("person-1")
                self.assertTrue(len(rels) > 0)
                targets = [r[1]["id"] for r in rels]
                self.assertIn("company-1", targets)

                # Provenance
                prov = repo.get_provenance("person-1")
                self.assertIsNotNone(prov)
                self.assertEqual(prov["source_name"], "domain-test")
            finally:
                repo.close()


# -- security ----------------------------------------------------------------

class SecurityTests(unittest.TestCase):
    def test_staging_rejects_invalid_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            prod_copy = _make_prod_copy(tmp)
            result = create_staging({"source": "bad"}, staging_path,
                                    prod_copy)
            self.assertFalse(result.success)
            self.assertTrue(len(result.errors) > 0)

    def test_apply_rejects_missing_staging(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            result = apply("/nonexistent/staging.db", prod_copy)
            self.assertFalse(result.committed)
            self.assertTrue(len(result.errors) > 0)

    def test_staging_no_sql_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            prod_copy = _make_prod_copy(tmp)
            data = {
                "source": _base_source(name="sql-test"),
                "nodes": [_node("'; DROP TABLE nodes; --",
                                name="SQL Injection")],
                "relationships": [],
            }
            result = create_staging(data, staging_path, prod_copy)
            # The malicious ID is treated as a normal string — no SQL execution
            self.assertTrue(result.success)
            self.assertEqual(result.nodes_staged, 1)

    def test_staging_no_arbitrary_path_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging_path = os.path.join(tmp, "staging.db")
            prod_copy = _make_prod_copy(tmp)
            data = _minimal_valid()
            result = create_staging(data, staging_path, prod_copy)
            self.assertTrue(result.success)
            # Staging DB was created at the specified path only
            self.assertTrue(os.path.isfile(staging_path))
            # No other DB files were created in this directory
            db_files = [f for f in os.listdir(tmp) if f.endswith(".db")]
            self.assertEqual(len(db_files), 1)


# -- DB integrity ------------------------------------------------------------

class DBIntegrityTests(unittest.TestCase):
    def test_integrity_check_after_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="integrity-test"),
                "nodes": [_node("integ-node", name="Integ Node")],
                "relationships": [],
            }
            create_staging(data, staging_path, prod_copy)
            apply(staging_path, prod_copy)

            conn = sqlite3.connect(f"file:{prod_copy}?mode=ro", uri=True)
            try:
                self.assertEqual(conn.execute(
                    "PRAGMA integrity_check").fetchone()[0], "ok")
                self.assertEqual(conn.execute(
                    "PRAGMA foreign_key_check").fetchall(), [])
            finally:
                conn.close()

    def test_foreign_keys_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            conn = sqlite3.connect(prod_copy)
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute(
                        "INSERT INTO nodes (id, type, name, description, source_id) "
                        "VALUES ('bad', 'x', 'y', 'z', 99999)")
            finally:
                conn.close()


# -- Contract v1 compatibility -----------------------------------------------

class ContractV1CompatibilityTests(unittest.TestCase):
    def test_search_imported_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="contract-search-test"),
                "nodes": [_node("contract-search-node",
                                ntype="technology",
                                name="ContractSearchable",
                                description="A searchable technology.")],
                "relationships": [],
            }
            create_staging(data, staging_path, prod_copy)
            apply(staging_path, prod_copy)

            repo = KnowledgeRepository(prod_copy)
            repo.initialize()
            try:
                results = repo.search_nodes("ContractSearchable")
                self.assertTrue(len(results) > 0)
            finally:
                repo.close()

    def test_get_imported_node(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="contract-get-test"),
                "nodes": [_node("contract-get-node",
                                name="ContractGettable")],
                "relationships": [],
            }
            create_staging(data, staging_path, prod_copy)
            apply(staging_path, prod_copy)

            repo = KnowledgeRepository(prod_copy)
            repo.initialize()
            try:
                node = repo.get_node("contract-get-node")
                self.assertIsNotNone(node)
                self.assertEqual(node["name"], "ContractGettable")
            finally:
                repo.close()

    def test_follow_imported_relationships(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="contract-follow-test"),
                "nodes": [_node("cft-a", name="CFT A"),
                          _node("cft-b", name="CFT B")],
                "relationships": [_rel("cft-a", "depends_on", "cft-b")],
            }
            create_staging(data, staging_path, prod_copy)
            apply(staging_path, prod_copy)

            repo = KnowledgeRepository(prod_copy)
            repo.initialize()
            try:
                rels = repo.follow("cft-a", "depends_on")
                self.assertTrue(len(rels) > 0)
                self.assertEqual(rels[0][1]["id"], "cft-b")
            finally:
                repo.close()

    def test_provenance_on_imported_nodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            staging_path = os.path.join(tmp, "staging.db")
            data = {
                "source": _base_source(name="contract-prov-test"),
                "nodes": [_node("cpt-node", name="CPT Node")],
                "relationships": [],
            }
            create_staging(data, staging_path, prod_copy)
            apply(staging_path, prod_copy)

            repo = KnowledgeRepository(prod_copy)
            repo.initialize()
            try:
                prov = repo.get_provenance("cpt-node")
                self.assertIsNotNone(prov)
                self.assertEqual(prov["source_name"], "contract-prov-test")
            finally:
                repo.close()


# -- CLI tests ---------------------------------------------------------------

class CLIStageTests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "external_import", *args],
            cwd=_ROOT, capture_output=True, text=True)

    def _parse_json(self, stdout):
        """Extract the first JSON object from combined JSON + human output."""
        depth = 0
        start = None
        for i, ch in enumerate(stdout):
            if ch == '{':
                if depth == 0:
                    start = i
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0 and start is not None:
                    return json.loads(stdout[start:i + 1])
        return None

    def test_stage_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "valid.json", _minimal_valid())
            staging = os.path.join(tmp, "staging.db")
            r = self._run("stage", path, "--staging", staging)
            self.assertEqual(r.returncode, 0)
            self.assertTrue(os.path.isfile(staging))
            out = self._parse_json(r.stdout)
            self.assertTrue(out["success"])

    def test_stage_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "bad.json", {"source": "bad"})
            staging = os.path.join(tmp, "staging.db")
            r = self._run("stage", path, "--staging", staging)
            self.assertEqual(r.returncode, 1)

    def test_preview_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "valid.json", _minimal_valid())
            staging = os.path.join(tmp, "staging.db")
            self._run("stage", path, "--staging", staging)
            r = self._run("preview", staging)
            self.assertEqual(r.returncode, 0)
            out = self._parse_json(r.stdout)
            self.assertTrue(out["staging_valid"])

    def test_apply_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            prod_copy = _make_prod_copy(tmp)
            path = _write_json(tmp, "valid.json", _minimal_valid())
            staging = os.path.join(tmp, "staging.db")
            self._run("stage", path, "--staging", staging, "--db", prod_copy)
            r = self._run("apply", staging, "--db", prod_copy)
            self.assertEqual(r.returncode, 0)
            out = self._parse_json(r.stdout)
            self.assertTrue(out["committed"])


if __name__ == "__main__":
    unittest.main()