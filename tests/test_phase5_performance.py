"""Phase 5: Performance & Scalability Behavioral Tests.

These are DETERMINISTIC tests (no timing assertions) that verify the
pipeline behaves correctly at various scales.  Separate from the
benchmark measurements in /tmp/phase5-bench/.
"""

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from external_import.validator import validate_external
from external_import.dry_run import dry_run
from external_import.staging import create_staging
from external_import.preview import preview
from external_import.apply import apply
from retrieval.repository import KnowledgeRepository

SEED_NODES = 1

import sys as _sys
_sys.path.insert(0, "/tmp/phase5-bench")
from generate import generate_dataset


def _build_seed_db(path):
    """Small, self-contained knowledge DB fixture used as the baseline."""
    repo = KnowledgeRepository(path)
    repo.initialize()
    sid = repo.add_source("seed", version="1.0")
    repo.add_node(
        "python", "technology", "Python",
        "A high-level, interpreted, general-purpose programming language.",
        source_id=sid,
    )
    repo.close()


def _prod_copy(tmp):
    seed_dir = os.path.join(tmp, "seed")
    os.makedirs(seed_dir, exist_ok=True)
    seed_path = os.path.join(seed_dir, "knowledge.db")
    _build_seed_db(seed_path)
    dst = os.path.join(tmp, "knowledge.db")
    shutil.copy2(seed_path, dst)
    return dst


def _prod_counts(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            "nodes": c.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
            "rels": c.execute("SELECT COUNT(*) FROM relationships").fetchone()[0],
            "sources": c.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
        }
    finally:
        c.close()


# ======================================================================
# Scale behavioral tests
# ======================================================================

class TestSmallScale(unittest.TestCase):
    """100 nodes, ~30 rels."""

    def setUp(self):
        self.data = generate_dataset(100, seed=42)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_validate(self):
        r = validate_external(self.data)
        self.assertTrue(r.valid)

    def test_dryrun(self):
        r = dry_run(self.data, db_path=_prod_copy(self.tmp))
        self.assertTrue(r.safe)
        self.assertEqual(r.new_nodes, 100)

    def test_staging(self):
        prod = _prod_copy(self.tmp)
        r = create_staging(self.data, os.path.join(self.tmp, "s.db"), prod)
        self.assertTrue(r.success)
        self.assertEqual(r.nodes_staged, 100)

    def test_preview(self):
        prod = _prod_copy(self.tmp)
        create_staging(self.data, os.path.join(self.tmp, "s.db"), prod)
        r = preview(os.path.join(self.tmp, "s.db"), prod)
        d = r.as_dict()
        self.assertTrue(d["safe"])
        self.assertEqual(d["new_nodes"], 100)

    def test_apply(self):
        prod = _prod_copy(self.tmp)
        create_staging(self.data, os.path.join(self.tmp, "s.db"), prod)
        ar = apply(os.path.join(self.tmp, "s.db"), prod)
        self.assertTrue(ar.committed)
        self.assertEqual(ar.imported["nodes"], 100)
        self.assertEqual(ar.imported["relationships"],
                         len(self.data["relationships"]))

    def test_idempotent(self):
        prod = _prod_copy(self.tmp)
        s1 = os.path.join(self.tmp, "s1.db")
        create_staging(self.data, s1, prod)
        apply(s1, prod)
        s2 = os.path.join(self.tmp, "s2.db")
        r2 = create_staging(self.data, s2, prod)
        self.assertTrue(r2.nodes_skipped > 0)
        ar2 = apply(s2, prod)
        self.assertEqual(ar2.imported["nodes"], 0)

    def test_prod_integrity(self):
        prod = _prod_copy(self.tmp)
        s = os.path.join(self.tmp, "s.db")
        create_staging(self.data, s, prod)
        apply(s, prod)
        c = sqlite3.connect(f"file:{prod}?mode=ro", uri=True)
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        c.close()


class TestMediumScale(unittest.TestCase):
    """1,000 nodes, ~300 rels."""

    def setUp(self):
        self.data = generate_dataset(1000, seed=42)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_validate(self):
        self.assertTrue(validate_external(self.data).valid)

    def test_staging_and_apply(self):
        prod = _prod_copy(self.tmp)
        s = os.path.join(self.tmp, "s.db")
        sr = create_staging(self.data, s, prod)
        self.assertTrue(sr.success)
        self.assertEqual(sr.nodes_staged, 1000)
        ar = apply(s, prod)
        self.assertTrue(ar.committed)
        self.assertEqual(ar.imported["nodes"], 1000)

    def test_search_after_import(self):
        from api.knowledge_api import KnowledgeAPI
        prod = _prod_copy(self.tmp)
        s = os.path.join(self.tmp, "s.db")
        create_staging(self.data, s, prod)
        apply(s, prod)
        api = KnowledgeAPI(db_path=prod)
        try:
            results = api.search("alpha")
            self.assertTrue(len(results) > 0)
        finally:
            api.close()

    def test_prod_integrity(self):
        prod = _prod_copy(self.tmp)
        s = os.path.join(self.tmp, "s.db")
        create_staging(self.data, s, prod)
        apply(s, prod)
        counts = _prod_counts(prod)
        self.assertEqual(counts["nodes"], SEED_NODES + 1000)
        c = sqlite3.connect(f"file:{prod}?mode=ro", uri=True)
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        c.close()


class TestLargeScale(unittest.TestCase):
    """10,000 nodes, ~3,000 rels."""

    def setUp(self):
        self.data = generate_dataset(10000, seed=42)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_validate(self):
        self.assertTrue(validate_external(self.data).valid)

    def test_staging(self):
        prod = _prod_copy(self.tmp)
        r = create_staging(self.data, os.path.join(self.tmp, "s.db"), prod)
        self.assertTrue(r.success)
        self.assertEqual(r.nodes_staged, 10000)

    def test_apply(self):
        prod = _prod_copy(self.tmp)
        s = os.path.join(self.tmp, "s.db")
        create_staging(self.data, s, prod)
        ar = apply(s, prod)
        self.assertTrue(ar.committed)
        self.assertEqual(ar.imported["nodes"], 10000)
        self.assertEqual(ar.imported["relationships"], 3000)

    def test_prod_integrity(self):
        prod = _prod_copy(self.tmp)
        s = os.path.join(self.tmp, "s.db")
        create_staging(self.data, s, prod)
        apply(s, prod)
        c = sqlite3.connect(f"file:{prod}?mode=ro", uri=True)
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        c.close()


class TestRelationshipHeavy(unittest.TestCase):
    """5,000 nodes, ~25,000 rels (5x ratio)."""

    def setUp(self):
        self.data = generate_dataset(5000, rel_ratio=5.0, seed=123)
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)

    def test_validate(self):
        self.assertTrue(validate_external(self.data).valid)

    def test_staging(self):
        prod = _prod_copy(self.tmp)
        r = create_staging(self.data, os.path.join(self.tmp, "s.db"), prod)
        self.assertTrue(r.success)
        self.assertEqual(r.nodes_staged, 5000)
        self.assertTrue(r.relationships_staged > 10000)

    def test_apply(self):
        prod = _prod_copy(self.tmp)
        s = os.path.join(self.tmp, "s.db")
        create_staging(self.data, s, prod)
        ar = apply(s, prod)
        self.assertTrue(ar.committed)
        self.assertEqual(ar.imported["nodes"], 5000)
        self.assertTrue(ar.imported["relationships"] > 10000)

    def test_prod_integrity(self):
        prod = _prod_copy(self.tmp)
        s = os.path.join(self.tmp, "s.db")
        create_staging(self.data, s, prod)
        apply(s, prod)
        c = sqlite3.connect(f"file:{prod}?mode=ro", uri=True)
        self.assertEqual(c.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(c.execute("PRAGMA foreign_key_check").fetchall(), [])
        c.close()


# ======================================================================
# Concurrency behavioral test
# ======================================================================

class TestConcurrencyReads(unittest.TestCase):
    """Reads remain correct under concurrent access."""

    def test_concurrent_reads_correct(self):
        import threading
        from api.knowledge_api import KnowledgeAPI

        prod = _prod_copy(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, os.path.dirname(prod))

        results_lock = threading.Lock()
        results = {"ok": 0, "errors": []}

        def reader(idx):
            try:
                api = KnowledgeAPI(db_path=prod)
                try:
                    r = api.search("python")
                    with results_lock:
                        results["ok"] += 1
                finally:
                    api.close()
            except Exception as e:
                with results_lock:
                    results["errors"].append(str(e))

        threads = [threading.Thread(target=reader, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        self.assertEqual(results["ok"], 5)
        self.assertEqual(results["errors"], [])


# ======================================================================
# Limits behavioral tests
# ======================================================================

class TestLimitsBehavioral(unittest.TestCase):
    """Verify the system handles edge cases without crashing."""

    def test_large_metadata(self):
        data = {
            "source": {"name": "test-lg-meta", "version": "1.0",
                       "location": "file:///x"},
            "nodes": [{"id": f"m{i}", "type": "concept", "name": f"M{i}",
                       "description": f"Node {i}",
                       "metadata": {"payload": "x" * 10000}}
                      for i in range(50)],
            "relationships": [],
        }
        self.assertTrue(validate_external(data).valid)

    def test_long_descriptions(self):
        data = {
            "source": {"name": "test-lg-desc", "version": "1.0",
                       "location": "file:///x"},
            "nodes": [{"id": f"d{i}", "type": "concept", "name": f"D{i}",
                       "description": "word " * 20000}
                      for i in range(20)],
            "relationships": [],
        }
        self.assertTrue(validate_external(data).valid)

    def test_many_relationships_per_node(self):
        nodes = [{"id": f"n{i}", "type": "concept", "name": f"N{i}",
                  "description": f"Node {i}"} for i in range(100)]
        rels = [{"source_node_id": "n0", "relationship_type": "related_to",
                 "target_node_id": f"n{i}"} for i in range(1, 100)]
        data = {"source": {"name": "test-many-rels", "version": "1.0",
                           "location": "file:///x"},
                "nodes": nodes, "relationships": rels}
        self.assertTrue(validate_external(data).valid)
        tmp = tempfile.mkdtemp()
        try:
            prod = _prod_copy(tmp)
            s = os.path.join(tmp, "s.db")
            r = create_staging(data, s, prod)
            self.assertTrue(r.success)
            self.assertEqual(r.relationships_staged, 99)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
