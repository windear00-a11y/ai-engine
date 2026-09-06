"""Tests for the ``python -m ai_engine`` CLI.

Covers: JSON stdout, exit codes (0 success / 1 on knowledge errors), help
output listing every command, stable/deterministic output, ``--db`` isolation
(temporary databases only), ``--limit``/``--type`` flags, error payloads
(``node_not_found``, ``invalid_argument``, ``invalid_relationship_type``),
and the read-only guarantee (file hash unchanged after every command).
One suite reads the canonical production database without mutating it.
"""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from retrieval.repository import KnowledgeRepository

# CLI search defaults (Phase 24: inlined in ai_engine.__main__._search_limit).
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100
_MAX_VERBOSE_BYTES = 16384


def _seed_file_db(db_path):
    """Create a temp DB: one source, five nodes, three relationships."""
    repo = KnowledgeRepository(db_path)
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
    repo.close()


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _temp_db():
    tmp = tempfile.TemporaryDirectory()
    db = os.path.join(tmp.name, "knowledge.db")
    _seed_file_db(db)
    return tmp, db


def _seed_bulk_db(db_path, count=250):
    """Many nodes matching one query, to exercise default-limit / caps."""
    repo = KnowledgeRepository(db_path)
    repo.initialize()
    sid = repo.add_source("bulk-source", version="1.0")
    for i in range(count):
        repo.add_node("bulk-%03d" % i, "concept", "Bulk Node %d" % i,
                      "bulk matching text", source_id=sid)
    repo.close()


def _seed_regression_db(db_path, scale=4000):
    """Load the large-evidence fixture, then scale blobs to megabyte scale."""
    fixture = os.path.join(_ROOT, "tests", "fixtures", "regression",
                           "large_evidence.json")
    with open(fixture, encoding="utf-8") as f:
        node = json.load(f)
    text = node["description"]
    evidence = node["evidence_references"][0]["evidence"]
    huge_desc = (text + " ") * scale
    huge_evidence = (evidence + "\n") * scale
    repo = KnowledgeRepository(db_path)
    repo.initialize()
    sid = repo.add_source("regression", version="1.0", location=fixture)
    repo.add_node(
        node["id"], node["type"], node["name"], huge_desc,
        source_id=sid,
        metadata={"evidence_references": [
            {"document": "reference/regression.rst",
             "section_path": ["Regression"],
             "location": {"path": "reference/regression.rst",
                          "line_start": 1, "line_end": 999999},
             "evidence": huge_evidence}]})
    repo.close()


def _run_cli(*args, cwd=_ROOT):
    return subprocess.run([sys.executable, "-m", "ai_engine", *args],
                          cwd=cwd, capture_output=True, text=True)


class CLITests(unittest.TestCase):
    def run_cli(self, *args, cwd=_ROOT):
        return _run_cli(*args, cwd=cwd)

    def test_help_lists_all_commands(self):
        r = self.run_cli("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        for cmd in ("init", "remember", "recall", "get", "provenance",
                    "inspect", "context", "serve", "backup", "restore"):
            self.assertIn(cmd, r.stdout)

    def test_inspect_json(self):
        tmp, db = _temp_db()
        try:
            r = self.run_cli("inspect", "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(r.stdout)
            self.assertEqual(data["source_count"], 1)
            self.assertEqual(data["node_count"], 5)
            self.assertEqual(data["relationship_count"], 3)
        finally:
            tmp.cleanup()

    def test_search_json_and_limit(self):
        tmp, db = _temp_db()
        try:
            self.run_cli("search", "exception", "--db", db)
            r = self.run_cli("search", "exception", "--db", db)
            data = json.loads(r.stdout)
            self.assertEqual(len(data), 4)
            ids = [n["id"] for n in data]
            self.assertIn("exceptions", ids)
            self.assertIn("keyboard", ids)
            r2 = self.run_cli("search", "exception", "--limit", "2",
                              "--db", db)
            self.assertEqual(len(json.loads(r2.stdout)), 2)
        finally:
            tmp.cleanup()

    def test_search_type_flag_errors_and_filter(self):
        tmp, db = _temp_db()
        try:
            ok = self.run_cli("search", "exception", "--type", "entity",
                              "--db", db)
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertTrue(all(n["type"] == "entity"
                                for n in json.loads(ok.stdout)))
            bad = self.run_cli("search", "exception", "--type", "bogus",
                               "--db", db)
            self.assertEqual(bad.returncode, 1)
            self.assertEqual(json.loads(bad.stdout)["error"],
                             "invalid_argument")
        finally:
            tmp.cleanup()

    def test_get_json(self):
        tmp, db = _temp_db()
        try:
            r = self.run_cli("get", "exceptions", "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(r.stdout)
            self.assertEqual(data["id"], "exceptions")
            self.assertEqual(data["type"], "concept")
            self.assertIn("relationships", data)
            self.assertIn("provenance", data)
        finally:
            tmp.cleanup()

    def test_get_missing_node_error_json(self):
        tmp, db = _temp_db()
        try:
            r = self.run_cli("get", "nope", "--db", db)
            self.assertEqual(r.returncode, 1)
            data = json.loads(r.stdout)
            self.assertEqual(data["error"], "node_not_found")
            self.assertEqual(data["node_id"], "nope")
        finally:
            tmp.cleanup()

    def test_related_json_and_limit(self):
        tmp, db = _temp_db()
        try:
            r = self.run_cli("related", "exceptions", "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(r.stdout)
            ids = [d["node"]["id"] for d in data]
            self.assertEqual(ids, sorted(ids))
            self.assertIn("exception-class", ids)
            self.assertIn("example", ids)
            r2 = self.run_cli("related", "exceptions", "--limit", "1",
                              "--db", db)
            self.assertEqual(len(json.loads(r2.stdout)), 1)
        finally:
            tmp.cleanup()

    def test_follow_json_and_type_filter(self):
        tmp, db = _temp_db()
        try:
            r = self.run_cli("follow", "exception-class", "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(r.stdout)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["target_node_id"], "exceptions")
            self.assertEqual(data[0]["relationship_type"], "extends")
            none = self.run_cli("follow", "exception-class", "--type",
                                "related_to", "--db", db)
            self.assertEqual(json.loads(none.stdout), [])
            bad = self.run_cli("follow", "exception-class", "--type",
                               "bogus", "--db", db)
            self.assertEqual(bad.returncode, 1)
            self.assertEqual(json.loads(bad.stdout)["error"],
                             "invalid_relationship_type")
        finally:
            tmp.cleanup()

    def test_provenance_json(self):
        tmp, db = _temp_db()
        try:
            r = self.run_cli("provenance", "exceptions", "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(r.stdout)
            self.assertEqual(data["node_id"], "exceptions")
            self.assertEqual(data["source_name"], "test-source")
            self.assertEqual(data["source_version"], "1.0")
        finally:
            tmp.cleanup()

    def test_output_deterministic(self):
        tmp, db = _temp_db()
        try:
            for args in (("inspect",), ("search", "exception"),
                         ("get", "exceptions"), ("related", "exceptions"),
                         ("follow", "exception-class"),
                         ("provenance", "exceptions")):
                a = self.run_cli(*args, "--db", db).stdout
                b = self.run_cli(*args, "--db", db).stdout
                self.assertEqual(a, b, args)
        finally:
            tmp.cleanup()

    def test_read_only_does_not_mutate_db(self):
        tmp, db = _temp_db()
        try:
            before = _sha256(db)
            for args in (("inspect",), ("search", "exception"),
                         ("get", "exceptions"), ("related", "exceptions"),
                         ("follow", "exception-class"),
                         ("provenance", "exceptions")):
                r = self.run_cli(*args, "--db", db)
                self.assertEqual(r.returncode, 0, args)
            self.assertEqual(_sha256(db), before)
        finally:
            tmp.cleanup()


class SearchSafetyTests(unittest.TestCase):
    """Compact, bounded, machine-readable search output (regression).

    Covers: compact default summaries, no large-evidence dumps, bounded
    default result count, working `--limit`, hard limit cap, `--verbose`
    byte budget, `get` full-information access, deterministic output,
    valid JSON, and database immutability during reads.
    """

    SUMMARY_FIELDS = {"id", "type", "name", "summary", "score"}

    def test_default_search_is_compact(self):
        tmp, db = _temp_db()
        try:
            r = _run_cli("search", "exception", "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(r.stdout)
            self.assertTrue(data)
            for entry in data:
                self.assertTrue(set(entry).issubset(self.SUMMARY_FIELDS),
                                "unexpected full fields: %s"
                                % sorted(entry))
                self.assertNotIn("evidence_references", entry)
                self.assertNotIn("provenance", entry)
                self.assertNotIn("relationships", entry)
                self.assertNotIn("description", entry)
        finally:
            tmp.cleanup()

    def test_large_evidence_not_dumped_by_default(self):
        tmp = tempfile.TemporaryDirectory()
        db = os.path.join(tmp.name, "knowledge.db")
        _seed_regression_db(db)
        try:
            r = _run_cli("search", "regression", "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(r.stdout)
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["id"], "regression-large-evidence")
            self.assertLessEqual(len(r.stdout.encode()), 4096)
            self.assertNotIn("evidence_references", r.stdout)
            self.assertIn("summary", data[0])
        finally:
            tmp.cleanup()

    def test_default_result_count_bounded(self):
        tmp = tempfile.TemporaryDirectory()
        db = os.path.join(tmp.name, "knowledge.db")
        _seed_bulk_db(db)
        try:
            r = _run_cli("search", "bulk", "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(json.loads(r.stdout)), DEFAULT_SEARCH_LIMIT)
        finally:
            tmp.cleanup()

    def test_limit_still_works(self):
        tmp = tempfile.TemporaryDirectory()
        db = os.path.join(tmp.name, "knowledge.db")
        _seed_bulk_db(db)
        try:
            r = _run_cli("search", "bulk", "--limit", "5", "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(json.loads(r.stdout)), 5)
        finally:
            tmp.cleanup()

    def test_limit_is_capped(self):
        tmp = tempfile.TemporaryDirectory()
        db = os.path.join(tmp.name, "knowledge.db")
        _seed_bulk_db(db)
        try:
            r = _run_cli("search", "bulk", "--limit", "99999", "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(len(json.loads(r.stdout)), MAX_SEARCH_LIMIT)
        finally:
            tmp.cleanup()

    def test_get_still_returns_full_information(self):
        tmp = tempfile.TemporaryDirectory()
        db = os.path.join(tmp.name, "knowledge.db")
        _seed_regression_db(db)
        try:
            r = _run_cli("get", "regression-large-evidence", "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(r.stdout)
            self.assertEqual(data["id"], "regression-large-evidence")
            self.assertIn("evidence_references", data)
            self.assertGreater(len(data["evidence_references"][0]["evidence"]),
                               1_000_000)
            self.assertGreater(len(data["description"]), 1_000_000)
        finally:
            tmp.cleanup()

    def test_verbose_mode_bounded(self):
        # Phase 24: the legacy ``--verbose`` flag is accepted for compat but the
        # CLI returns compact, bounded, machine-readable JSON in both modes.
        tmp = tempfile.TemporaryDirectory()
        db = os.path.join(tmp.name, "knowledge.db")
        _seed_regression_db(db)
        try:
            r = _run_cli("search", "regression", "--verbose", "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertLessEqual(len(r.stdout.encode()),
                                 _MAX_VERBOSE_BYTES + 8192)
            json.loads(r.stdout)  # still valid JSON
            self.assertNotIn("LARGE-EVIDENCE-REGRESSION-BLOB " * 100,
                             r.stdout)
            self.assertNotIn("REGION-EVIDENCE-BLOB " * 100, r.stdout)
        finally:
            tmp.cleanup()

    def test_get_still_returns_full_information(self):
        # Full-information access is provided by ``get`` (compact search
        # output never dumps large evidence blobs).
        tmp = tempfile.TemporaryDirectory()
        db = os.path.join(tmp.name, "knowledge.db")
        _seed_regression_db(db)
        try:
            r = _run_cli("get", "regression-large-evidence", "--db", db)
            self.assertEqual(r.returncode, 0, r.stderr)
            data = json.loads(r.stdout)
            self.assertEqual(data["id"], "regression-large-evidence")
            self.assertIn("evidence_references", data)
            self.assertGreater(len(data["evidence_references"][0]["evidence"]),
                               1_000_000)
            self.assertGreater(len(data["description"]), 1_000_000)
        finally:
            tmp.cleanup()

    def test_compact_output_deterministic(self):
        tmp, db = _temp_db()
        try:
            a = _run_cli("search", "exception", "--db", db).stdout
            b = _run_cli("search", "exception", "--db", db).stdout
            self.assertEqual(a, b)
        finally:
            tmp.cleanup()

    def test_verbose_output_deterministic(self):
        tmp, db = _temp_db()
        try:
            a = _run_cli("search", "exception", "--verbose",
                             "--db", db).stdout
            b = _run_cli("search", "exception", "--verbose",
                             "--db", db).stdout
            self.assertEqual(a, b)
        finally:
            tmp.cleanup()

    def test_search_does_not_modify_database(self):
        tmp = tempfile.TemporaryDirectory()
        db = os.path.join(tmp.name, "knowledge.db")
        _seed_regression_db(db)
        try:
            before = _sha256(db)
            _run_cli("search", "regression", "--db", db)
            _run_cli("search", "regression", "--verbose", "--db", db)
            _run_cli("get", "regression-large-evidence", "--db", db)
            self.assertEqual(_sha256(db), before)
        finally:
            tmp.cleanup()


class ProductionDBTests(unittest.TestCase):
    PROD = os.path.join(_ROOT, "database", "knowledge.db")

    def test_production_inspect_reads_real_counts(self):
        r = subprocess.run([sys.executable, "-m", "ai_engine", "inspect"],
                           cwd=_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertGreaterEqual(data["source_count"], 6)
        self.assertGreaterEqual(data["node_count"], 4846)
        self.assertGreaterEqual(data["relationship_count"], 55)

    def test_production_get_known_node(self):
        r = subprocess.run([sys.executable, "-m", "ai_engine", "get",
                            "exceptions"],
                           cwd=_ROOT, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["id"], "exceptions")
        self.assertEqual(data["type"], "concept")

    def test_production_read_does_not_mutate(self):
        before = _sha256(self.PROD)
        subprocess.run([sys.executable, "-m", "ai_engine", "search",
                        "exception"], cwd=_ROOT, capture_output=True,
                       text=True)
        self.assertEqual(_sha256(self.PROD), before)


if __name__ == "__main__":
    unittest.main()