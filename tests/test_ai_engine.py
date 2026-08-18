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


class CLITests(unittest.TestCase):
    def run_cli(self, *args, cwd=_ROOT):
        return subprocess.run(
            [sys.executable, "-m", "ai_engine", *args],
            cwd=cwd, capture_output=True, text=True)

    def test_help_lists_all_commands(self):
        r = self.run_cli("--help")
        self.assertEqual(r.returncode, 0, r.stderr)
        for cmd in ("search", "get", "related", "follow",
                    "provenance", "inspect"):
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