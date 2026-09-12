"""Comprehensive tests for external import validation and dry-run (Phase 2).

Covers: JSON structure, source validation, node validation, relationship
validation, referential integrity, duplicate detection, self-references,
dry-run conflict detection, determinism, isolation, CLI, multi-domain
fixtures, and read-only safety.

All dry-run tests run against an isolated, seeded database created in a
temporary directory -- no committed repository database is required.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from external_import.validator import (
    validate_external,
    ExternalValidationResult,
)
from external_import.dry_run import (
    dry_run,
    _content_hash,
    _read_only_conn,
)
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


def _build_seed_db(path):
    """Small, self-contained DB with the python/react/javascript fixtures the
    dry-run classification tests rely on."""
    repo = KnowledgeRepository(path)
    repo.initialize()
    sid = repo.add_source("python-core", version="1.0")
    repo.add_node(
        "python", "technology", "Python",
        "A high-level, interpreted, general-purpose programming language.",
        source_id=sid,
    )
    repo.add_node(
        "react", "technology", "React",
        "A JavaScript library for building user interfaces.", source_id=sid)
    repo.add_node(
        "javascript", "technology", "JavaScript",
        "A high-level, interpreted programming language.", source_id=sid)
    repo.add_relationship("react", "related_to", "javascript", "seed")
    repo.close()


def _db_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


# -- validation: structure -------------------------------------------------

class StructureValidationTests(unittest.TestCase):
    def test_non_dict_returns_invalid(self):
        result = validate_external([1, 2, 3])
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "invalid_root" for e in result.errors))

    def test_none_returns_invalid(self):
        result = validate_external(None)
        self.assertFalse(result.valid)

    def test_valid_minimal(self):
        result = validate_external(_minimal_valid())
        self.assertTrue(result.valid)
        self.assertEqual(result.errors, [])
        self.assertEqual(len(result.nodes), 2)
        self.assertEqual(len(result.relationships), 1)

    def test_empty_nodes_list(self):
        data = _minimal_valid()
        data["nodes"] = []
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "nodes" for e in result.errors))

    def test_missing_nodes(self):
        data = {"source": _base_source()}
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "nodes" for e in result.errors))


# -- validation: source ----------------------------------------------------

class SourceValidationTests(unittest.TestCase):
    def test_missing_source(self):
        data = {"nodes": [_node("a")]}
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "source_metadata" for e in result.errors))

    def test_source_not_object(self):
        data = {"source": "bad", "nodes": [_node("a")]}
        result = validate_external(data)
        self.assertFalse(result.valid)

    def test_source_name_missing(self):
        data = {"source": {"version": "1.0"}, "nodes": [_node("a")]}
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any("source.name" in e.path for e in result.errors))

    def test_source_name_empty(self):
        data = {"source": {"name": ""}, "nodes": [_node("a")]}
        result = validate_external(data)
        self.assertFalse(result.valid)

    def test_source_version_not_string(self):
        data = {"source": {"name": "x", "version": 42}, "nodes": [_node("a")]}
        result = validate_external(data)
        self.assertFalse(result.valid)

    def test_source_location_not_string(self):
        data = {"source": {"name": "x", "location": 42}, "nodes": [_node("a")]}
        result = validate_external(data)
        self.assertFalse(result.valid)

    def test_source_minimal(self):
        data = {"source": {"name": "x"}, "nodes": [_node("a")]}
        result = validate_external(data)
        self.assertTrue(result.valid)

    def test_source_normalized(self):
        data = {"source": {"name": "  Test  ", "version": "2.0"}, "nodes": [_node("a")]}
        result = validate_external(data)
        self.assertTrue(result.valid)
        self.assertEqual(result.source["name"], "Test")
        self.assertEqual(result.source["version"], "2.0")


# -- validation: nodes -----------------------------------------------------

class NodeValidationTests(unittest.TestCase):
    def test_node_id_required(self):
        data = _minimal_valid()
        data["nodes"][0].pop("id")
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "node_id" for e in result.errors))

    def test_node_type_required(self):
        data = _minimal_valid()
        data["nodes"][0].pop("type")
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "node_type" for e in result.errors))

    def test_node_type_empty_string(self):
        data = _minimal_valid()
        data["nodes"][0]["type"] = ""
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "node_type" for e in result.errors))

    def test_node_name_required(self):
        data = _minimal_valid()
        data["nodes"][0].pop("name")
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "node_name" for e in result.errors))

    def test_node_description_optional(self):
        data = _minimal_valid()
        data["nodes"][0].pop("description")
        result = validate_external(data)
        self.assertTrue(result.valid)

    def test_node_description_not_string(self):
        data = _minimal_valid()
        data["nodes"][0]["description"] = 42
        result = validate_external(data)
        self.assertFalse(result.valid)

    def test_duplicate_node_ids(self):
        data = _minimal_valid()
        data["nodes"].append(_node("a", name="Duplicate"))
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "duplicate_id" for e in result.errors))

    def test_custom_type_accepted(self):
        data = _minimal_valid()
        data["nodes"][0]["type"] = "my_custom_type"
        result = validate_external(data)
        self.assertTrue(result.valid)

    def test_metadata_must_be_dict(self):
        data = _minimal_valid()
        data["nodes"][0]["metadata"] = "not a dict"
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "node_metadata" for e in result.errors))

    def test_metadata_json_compatible(self):
        data = _minimal_valid()
        data["nodes"][0]["metadata"] = {"key": "value", "num": 42}
        result = validate_external(data)
        self.assertTrue(result.valid)

    def test_metadata_non_json_compatible(self):
        data = _minimal_valid()
        data["nodes"][0]["metadata"] = {"key": object()}
        result = validate_external(data)
        self.assertFalse(result.valid)

    def test_malformed_node(self):
        data = _minimal_valid()
        data["nodes"][0] = "not-a-dict"
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "malformed_node" for e in result.errors))

    def test_node_type_lowercased(self):
        data = _minimal_valid()
        data["nodes"][0]["type"] = "Concept"
        result = validate_external(data)
        self.assertTrue(result.valid)
        self.assertEqual(result.nodes[0]["type"], "concept")

    def test_extra_keys_preserved_as_metadata(self):
        data = _minimal_valid()
        data["nodes"][0]["custom_field"] = "preserved"
        result = validate_external(data)
        self.assertTrue(result.valid)
        self.assertEqual(result.nodes[0]["metadata"]["custom_field"], "preserved")


# -- validation: relationships ---------------------------------------------

class RelationshipValidationTests(unittest.TestCase):
    def test_relationships_optional(self):
        data = {"source": _base_source(), "nodes": [_node("a")]}
        result = validate_external(data)
        self.assertTrue(result.valid)
        self.assertEqual(result.relationships, [])

    def test_relationship_source_required(self):
        data = _minimal_valid()
        data["relationships"][0].pop("source_node_id")
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "relationship_source" for e in result.errors))

    def test_relationship_target_required(self):
        data = _minimal_valid()
        data["relationships"][0].pop("target_node_id")
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "relationship_target" for e in result.errors))

    def test_relationship_type_required(self):
        data = _minimal_valid()
        data["relationships"][0].pop("relationship_type")
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "relationship_type" for e in result.errors))

    def test_relationship_type_empty(self):
        data = _minimal_valid()
        data["relationships"][0]["relationship_type"] = ""
        result = validate_external(data)
        self.assertFalse(result.valid)

    def test_relationship_not_object(self):
        data = _minimal_valid()
        data["relationships"][0] = "not-a-dict"
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "relationship_structure" for e in result.errors))

    def test_relationships_not_list(self):
        data = _minimal_valid()
        data["relationships"] = "not-a-list"
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "relationships" for e in result.errors))

    def test_label_optional_string(self):
        data = _minimal_valid()
        data["relationships"][0]["label"] = "test label"
        result = validate_external(data)
        self.assertTrue(result.valid)
        self.assertEqual(result.relationships[0]["label"], "test label")

    def test_label_optional_null(self):
        data = _minimal_valid()
        data["relationships"][0]["label"] = None
        result = validate_external(data)
        self.assertTrue(result.valid)

    def test_label_default_none(self):
        data = _minimal_valid()
        result = validate_external(data)
        self.assertIsNone(result.relationships[0]["label"])

    def test_custom_relationship_type_accepted(self):
        data = _minimal_valid()
        data["relationships"][0]["relationship_type"] = "custom_rel"
        result = validate_external(data)
        self.assertTrue(result.valid)

    def test_duplicate_relationship(self):
        data = _minimal_valid()
        data["relationships"].append(_rel("a", "related_to", "b"))
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "duplicate_relationship" for e in result.errors))

    def test_self_reference_warning(self):
        data = _minimal_valid()
        data["relationships"].append(_rel("a", "related_to", "a"))
        result = validate_external(data)
        self.assertTrue(result.valid)
        self.assertTrue(any(w.code == "self_reference" for w in result.warnings))


# -- validation: referential integrity -------------------------------------

class ReferentialIntegrityTests(unittest.TestCase):
    def test_source_not_in_nodes(self):
        data = _minimal_valid()
        data["relationships"][0]["source_node_id"] = "ghost"
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "relationship_source_missing"
                            for e in result.errors))

    def test_target_not_in_nodes(self):
        data = _minimal_valid()
        data["relationships"][0]["target_node_id"] = "ghost"
        result = validate_external(data)
        self.assertFalse(result.valid)
        self.assertTrue(any(e.code == "relationship_target_missing"
                            for e in result.errors))


# -- content hash -----------------------------------------------------------

class ContentHashTests(unittest.TestCase):
    def test_same_content_same_hash(self):
        a = {"type": "concept", "name": "X", "description": "d"}
        b = {"type": "concept", "name": "X", "description": "d"}
        self.assertEqual(_content_hash(a), _content_hash(b))

    def test_different_content_different_hash(self):
        a = {"type": "concept", "name": "X"}
        b = {"type": "concept", "name": "Y"}
        self.assertNotEqual(_content_hash(a), _content_hash(b))

    def test_key_order_irrelevant(self):
        a = {"b": 2, "a": 1}
        b = {"a": 1, "b": 2}
        self.assertEqual(_content_hash(a), _content_hash(b))


# -- dry-run: basic --------------------------------------------------------

class DryRunBasicTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "knowledge.db")
        _build_seed_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_valid_data_no_conflicts(self):
        data = _minimal_valid()
        report = dry_run(data, db_path=self.db)
        self.assertTrue(report.safe)
        self.assertTrue(report.validated)
        self.assertTrue(report.db_opened)
        self.assertEqual(report.nodes_total, 2)
        self.assertEqual(report.relationships_total, 1)

    def test_invalid_data_not_safe(self):
        data = {"source": "bad", "nodes": []}
        report = dry_run(data, db_path=self.db)
        self.assertFalse(report.safe)
        self.assertFalse(report.validated)

    def test_non_dict_input(self):
        report = dry_run("not a dict")
        self.assertFalse(report.safe)

    def test_report_is_deterministic(self):
        data = _minimal_valid()
        r1 = dry_run(data, db_path=self.db)
        r2 = dry_run(data, db_path=self.db)
        self.assertEqual(r1.as_dict(), r2.as_dict())

    def test_report_json_is_deterministic(self):
        data = _minimal_valid()
        r1 = dry_run(data, db_path=self.db)
        r2 = dry_run(data, db_path=self.db)
        self.assertEqual(r1.as_json(), r2.as_json())


# -- dry-run: production DB conflicts --------------------------------------

class DryRunConflictTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "knowledge.db")
        _build_seed_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_existing_node_detected(self):
        data = {
            "source": _base_source(name="conflict-test"),
            "nodes": [_node("python", name="Python", description="A language")],
            "relationships": [],
        }
        report = dry_run(data, db_path=self.db)
        self.assertTrue(report.db_opened)
        self.assertGreater(report.existing_nodes, 0)
        self.assertEqual(report.existing_nodes, 1)

    def test_new_node_detected(self):
        data = {
            "source": _base_source(name="new-node-test"),
            "nodes": [_node("totally-unique-node-xyz-999",
                            name="Unique Node")],
            "relationships": [],
        }
        report = dry_run(data, db_path=self.db)
        self.assertEqual(report.new_nodes, 1)

    def test_content_match_detected(self):
        # "python" node exists in production DB with known content
        data = {
            "source": _base_source(name="match-test"),
            "nodes": [_node("python", ntype="technology", name="Python",
                            description="A high-level, interpreted, "
                                        "general-purpose programming language.")],
            "relationships": [],
        }
        report = dry_run(data, db_path=self.db)
        self.assertGreater(report.content_match, 0)

    def test_content_mismatch_detected(self):
        data = {
            "source": _base_source(name="mismatch-test"),
            "nodes": [_node("python", name="Python MODIFIED",
                            description="A different description.")],
            "relationships": [],
        }
        report = dry_run(data, db_path=self.db)
        self.assertGreater(report.content_mismatch, 0)
        self.assertIn("python", report.conflicting_nodes)
        self.assertFalse(report.safe)

    def test_mixed_new_and_existing(self):
        data = {
            "source": _base_source(name="mixed-test"),
            "nodes": [
                _node("python", name="Python"),           # existing
                _node("unique-new-node-abc", name="New"),  # new
            ],
            "relationships": [],
        }
        report = dry_run(data, db_path=self.db)
        self.assertGreater(report.existing_nodes, 0)
        self.assertGreater(report.new_nodes, 0)

    def test_existing_relationship_detected(self):
        # react -> javascript "related_to" exists in production DB
        data = {
            "source": _base_source(name="rel-test"),
            "nodes": [_node("react", name="React"),
                      _node("javascript", name="JavaScript")],
            "relationships": [
                _rel("react", "related_to", "javascript"),
            ],
        }
        report = dry_run(data, db_path=self.db)
        self.assertGreater(report.existing_relationships, 0)

    def test_new_relationship_detected(self):
        data = {
            "source": _base_source(name="new-rel-test"),
            "nodes": [_node("react", name="React"),
                      _node("python", name="Python")],
            "relationships": [
                _rel("react", "custom_rel", "python"),
            ],
        }
        report = dry_run(data, db_path=self.db)
        self.assertGreater(report.new_relationships, 0)

    def test_source_name_exists(self):
        # "python-core" source exists in production DB
        data = {
            "source": _base_source(name="python-core"),
            "nodes": [_node("a", name="A")],
            "relationships": [],
        }
        report = dry_run(data, db_path=self.db)
        self.assertTrue(report.existing_source)
        self.assertFalse(report.new_source)


# -- dry-run: isolation / safety -------------------------------------------

class DryRunSafetyTests(unittest.TestCase):
    """Dry-run never mutates the isolated seeded database and only reads it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "knowledge.db")
        _build_seed_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_runtime_db_hash_unchanged(self):
        before = _db_hash(self.db)
        data = {
            "source": _base_source(name="safety-test"),
            "nodes": [_node("python", name="Python MODIFIED",
                            description="TAMPERED")],
            "relationships": [],
        }
        dry_run(data, db_path=self.db)
        after = _db_hash(self.db)
        self.assertEqual(before, after)

    def test_read_only_conn_rejects_writes(self):
        conn = _read_only_conn(self.db)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO nodes (id, type, name, description) "
                             "VALUES ('hack', 'x', 'y', 'z')")
        finally:
            conn.close()


# -- dry-run: determinism --------------------------------------------------

class DryRunDeterminismTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "knowledge.db")
        _build_seed_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_repeated_runs_identical(self):
        data = _minimal_valid()
        reports = [dry_run(data, db_path=self.db) for _ in range(5)]
        dicts = [r.as_dict() for r in reports]
        self.assertTrue(all(d == dicts[0] for d in dicts))

    def test_no_timestamps_in_report(self):
        data = _minimal_valid()
        report = dry_run(data, db_path=self.db)
        report_json = report.as_json()
        self.assertNotIn("T", report_json.split('"source_name"')[0]
                         if '"source_name"' in report_json else report_json)

    def test_sorted_output(self):
        data = _minimal_valid()
        report = dry_run(data, db_path=self.db)
        d = report.as_dict()
        self.assertEqual(d["errors"], sorted(d["errors"],
                                             key=lambda e: (e.get("code", ""),
                                                           e.get("path", ""))))
        self.assertEqual(d["warnings"], sorted(d["warnings"],
                                               key=lambda w: (w.get("code", ""),
                                                             w.get("path", ""))))


# -- dry-run: DB unavailable -----------------------------------------------

class DryRunDBUnavailableTests(unittest.TestCase):
    def test_missing_db_graceful(self):
        data = _minimal_valid()
        report = dry_run(data, db_path="/nonexistent/knowledge.db")
        self.assertTrue(report.safe)
        self.assertFalse(report.db_opened)
        self.assertEqual(report.new_nodes, 2)
        self.assertTrue(any(w["code"] == "db_unavailable"
                            for w in report.warnings))


# -- multi-domain fixtures -------------------------------------------------

class MultiDomainFixturesTests(unittest.TestCase):
    """Test that all 7 domain types + custom types validate and dry-run."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self._tmp.name, "knowledge.db")
        _build_seed_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    DOMAIN_TYPES = [
        ("person", "Alice Smith", "A software engineer"),
        ("company", "Acme Corp", "A technology company"),
        ("product", "Widget Pro", "A productivity tool"),
        ("document", "RFC 7231", "HTTP semantics specification"),
        ("event", "PyCon 2024", "Annual Python conference"),
        ("research_paper", "Attention Is All You Need", "Transformer paper"),
        ("location", "San Francisco", "City in California"),
    ]

    def test_all_domain_types_validate(self):
        for ntype, name, desc in self.DOMAIN_TYPES:
            data = {
                "source": _base_source(name=f"domain-{ntype}"),
                "nodes": [_node(f"n-{ntype}", ntype=ntype, name=name,
                                description=desc)],
                "relationships": [],
            }
            result = validate_external(data)
            self.assertTrue(result.valid, f"type {ntype} should validate")

    def test_all_domain_types_dry_run(self):
        for ntype, name, desc in self.DOMAIN_TYPES:
            data = {
                "source": _base_source(name=f"domain-{ntype}"),
                "nodes": [_node(f"n-{ntype}", ntype=ntype, name=name,
                                description=desc)],
                "relationships": [],
            }
            report = dry_run(data, db_path=self.db)
            self.assertTrue(report.safe, f"type {ntype} dry-run should be safe")

    def test_custom_type_validate_and_dry_run(self):
        data = {
            "source": _base_source(name="custom-type-test"),
            "nodes": [_node("custom-1", ntype="my_domain_type",
                            name="Custom Node")],
            "relationships": [],
        }
        self.assertTrue(validate_external(data).valid)
        report = dry_run(data, db_path=self.db)
        self.assertTrue(report.safe)

    def test_cross_domain_relationships(self):
        data = {
            "source": _base_source(name="cross-domain"),
            "nodes": [
                _node("person-1", ntype="person", name="Alice"),
                _node("company-1", ntype="company", name="Acme"),
                _node("product-1", ntype="product", name="Widget"),
                _node("event-1", ntype="event", name="Launch"),
            ],
            "relationships": [
                _rel("person-1", "works_at", "company-1"),
                _rel("company-1", "created", "product-1"),
                _rel("person-1", "authored", "product-1"),
                _rel("person-1", "located_at", "company-1"),
            ],
        }
        result = validate_external(data)
        self.assertTrue(result.valid)
        report = dry_run(data, db_path=self.db)
        self.assertTrue(report.safe)
        self.assertEqual(report.relationships_total, 4)


# -- CLI tests -------------------------------------------------------------

class CLITests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "external_import", *args],
            cwd=_ROOT, capture_output=True, text=True)

    def test_validate_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "valid.json", _minimal_valid())
            r = self._run("validate", path)
            self.assertEqual(r.returncode, 0)
            out = json.loads(r.stdout)
            self.assertTrue(out["valid"])

    def test_validate_invalid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "bad.json", {"source": "bad"})
            r = self._run("validate", path)
            self.assertEqual(r.returncode, 1)
            out = json.loads(r.stdout)
            self.assertFalse(out["valid"])

    def test_dry_run_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "valid.json", _minimal_valid())
            r = self._run("dry-run", path)
            self.assertEqual(r.returncode, 0)

    def test_file_not_found(self):
        r = self._run("validate", "/nonexistent/file.json")
        self.assertEqual(r.returncode, 1)

    def test_invalid_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.json")
            with open(path, "w") as f:
                f.write("{not json")
            r = self._run("validate", path)
            self.assertEqual(r.returncode, 1)

    def test_dry_run_custom_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "valid.json", _minimal_valid())
            db = os.path.join(tmp, "test.db")
            r = self._run("dry-run", path, "--db", db)
            # Missing DB should still work (graceful)
            self.assertEqual(r.returncode, 0)

    def test_dry_run_produces_human_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_json(tmp, "valid.json", _minimal_valid())
            r = self._run("dry-run", path)
            self.assertIn("External import dry-run", r.stdout)
            self.assertIn("SAFE:", r.stdout)


if __name__ == "__main__":
    unittest.main()
