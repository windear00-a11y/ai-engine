"""Tests for the Controlled First SQLite Import (``importing.importer``).

Covers: successful atomic import, persistence after reopen, backup creation
(timestamped, never overwritten, verified hash), refusal of malformed /
unverified plans before any write, atomic rollback on repository constraint
failures (duplicate PK vs existing database), provenance retention, existing
knowledge preservation, post-import counts, relationship traversal, and the
production safety of the ``import`` CLI command.

Every test uses a temporary database file -- the production
``database/knowledge.db`` is never opened or written here.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from importing.importer import import_plan, backup_database
from retrieval.repository import KnowledgeRepository


# -- helpers ---------------------------------------------------------------

def prov(cid="cand-1"):
    return [{
        "candidate_id": cid,
        "document": "doc.rst",
        "section_path": ["Section"],
        "location": {"path": "doc.rst", "line_start": 1, "line_end": 1},
        "evidence": "evidence for %s" % cid,
    }]


def node(nid, ntype="concept", name=None, description=None, provenance=None):
    return {
        "id": nid,
        "type": ntype,
        "name": name or nid,
        "description": description or "description of %s" % nid,
        "provenance": provenance if provenance is not None else prov("c-%s" % nid),
    }


def rel(src, rtype, tgt, label=None):
    return {
        "source_node_id": src,
        "relationship_type": rtype,
        "target_node_id": tgt,
        "label": label or "%s %s %s" % (src, rtype, tgt),
        "target_name": tgt,
        "provenance_identity": "id-1",
    }


def build_plan(nodes, rels, source="/tmp/candidate-output"):
    node_ids = {n["id"] for n in nodes}
    return {
        "source": source,
        "read_only_guard": {
            "sqlite_writes_forbidden": True,
            "note": "preview only -- no database was written",
        },
        "preview": {
            "proposed_nodes": nodes,
            "proposed_nodes_count": len(nodes),
            "proposed_nodes_by_type": dict(Counter(n["type"] for n in nodes)),
            "proposed_relationships": rels,
            "proposed_relationships_count": len(rels),
            "proposed_relationships_by_type": dict(
                Counter(r["relationship_type"] for r in rels)),
            "reference_integrity": {
                "dangling_relationship_targets": sum(
                    1 for r in rels if r["target_node_id"] not in node_ids),
            },
            "summary": {},
        },
        "decisions": [],
        "identities": [],
    }


def seed_db(path):
    """Create a fresh temp DB with existing knowledge (2 nodes, 1 rel)."""
    repo = KnowledgeRepository(path)
    repo.initialize()
    sid = repo.add_source("existing-source", version="1.0",
                          location="/tmp/existing.json")
    repo.add_node("existing-1", "concept", "Existing Concept",
                  "desc", source_id=sid)
    repo.add_node("existing-2", "technology", "Existing Tech",
                  "desc", source_id=sid)
    repo.add_relationship("existing-1", "related_to", "existing-2", "label")
    repo.close()


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def counts(path):
    conn = KnowledgeRepository(path)
    conn.initialize()
    try:
        return {
            "sources": conn.conn.execute(
                "SELECT COUNT(*) FROM sources").fetchone()[0],
            "nodes": conn.conn.execute(
                "SELECT COUNT(*) FROM nodes").fetchone()[0],
            "relationships": conn.conn.execute(
                "SELECT COUNT(*) FROM relationships").fetchone()[0],
        }
    finally:
        conn.close()


# -- successful import -----------------------------------------------------

class SuccessfulImportTests(unittest.TestCase):
    def _import_ok(self, tmp):
        db = os.path.join(tmp, "knowledge.db")
        seed_db(db)
        plan = build_plan(
            [node("a", "concept"), node("b", "technology"),
             node("c", "dependency")],
            [rel("a", "related_to", "b")])
        result = import_plan(plan, db, backup_dir=os.path.join(tmp, "backups"))
        self.assertTrue(result.committed, result.errors)
        self.assertTrue(result.plan_verified)
        self.assertFalse(result.rollback)
        return db, plan, result

    def test_import_commits_atomically(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, plan, result = self._import_ok(tmp)
            self.assertEqual(result.before["source_count"], 1)
            self.assertEqual(result.before["node_count"], 2)
            self.assertEqual(result.before["relationship_count"], 1)
            self.assertEqual(result.after["source_count"], 2)
            self.assertEqual(result.after["node_count"], 5)
            self.assertEqual(result.after["relationship_count"], 2)
            self.assertEqual(result.imported["nodes"], 3)
            self.assertEqual(result.imported["relationships"], 1)
            self.assertEqual(result.imported["nodes_by_type"],
                             {"concept": 1, "technology": 1, "dependency": 1})
            self.assertEqual(result.imported["relationships_by_type"],
                             {"related_to": 1})

    def test_persistence_after_reopen(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, plan, result = self._import_ok(tmp)
            repo = KnowledgeRepository(db)
            repo.initialize()
            try:
                self.assertIsNotNone(repo.get_node("a"))
                self.assertEqual(repo.get_node("a")["type"], "concept")
                self.assertIsNotNone(repo.get_node("existing-1"))
                self.assertEqual(counts(db)["nodes"], 5)
            finally:
                repo.close()

    def test_existing_knowledge_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, plan, result = self._import_ok(tmp)
            post = result.post_import
            self.assertTrue(post["existing_knowledge_preserved"]["ok"])
            self.assertEqual(post["existing_knowledge_preserved"]["lost"], [])
            repo = KnowledgeRepository(db)
            repo.initialize()
            try:
                node = repo.get_node("existing-1")
                self.assertEqual(node["name"], "Existing Concept")
                self.assertEqual(len(repo.relationships_of("existing-1")), 1)
            finally:
                repo.close()

    def test_provenance_retained(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, plan, result = self._import_ok(tmp)
            repo = KnowledgeRepository(db)
            repo.initialize()
            try:
                node = repo.get_node("a")
                prov_ = node["provenance"]
                for key in ("source_id", "source_name", "source_version",
                            "source_location", "imported_at"):
                    self.assertIn(key, prov_)
                self.assertEqual(node["evidence_reference_count"], 1)
                self.assertEqual(node["evidence_references"][0]["candidate_id"],
                                 "c-a")
            finally:
                repo.close()
            post = result.post_import
            self.assertTrue(post["provenance"]["complete"])
            self.assertEqual(post["provenance"]["missing"], 0)

    def test_post_import_counts_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, plan, result = self._import_ok(tmp)
            post = result.post_import
            self.assertTrue(post["reopened"])
            self.assertTrue(post["foreign_keys_enabled"])
            self.assertEqual(post["imported_node_count"], 3)
            self.assertEqual(post["imported_relationship_count"], 1)
            self.assertEqual(post["counts"]["source_count"], 2)
            self.assertEqual(post["counts"]["node_count"], 5)
            self.assertEqual(post["counts"]["relationship_count"], 2)
            self.assertEqual(post["dangling_relationship_sources"], 0)
            self.assertEqual(post["duplicate_primary_keys"], 0)

    def test_relationship_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, plan, result = self._import_ok(tmp)
            repo = KnowledgeRepository(db)
            repo.initialize()
            try:
                follows = repo.follow("a", "related_to")
                self.assertEqual(len(follows), 1)
                self.assertEqual(follows[0][0]["target"], "b")
                self.assertEqual(follows[0][1]["id"], "b")
            finally:
                repo.close()
            self.assertTrue(
                result.post_import["relationship_traversal"]["ok"])


# -- backups ---------------------------------------------------------------

class BackupTests(unittest.TestCase):
    def test_backup_created_and_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            seed_db(db)
            result = import_plan(
                build_plan([node("a")], []), db,
                backup_dir=os.path.join(tmp, "backups"))
            self.assertTrue(result.committed)
            backup = result.backup
            self.assertIsNotNone(backup)
            self.assertTrue(os.path.isfile(backup["path"]))
            self.assertGreater(backup["size"], 0)
            self.assertTrue(backup["sha256"])
            # the backup is a consistent pre-import snapshot: same logical
            # content and a clean integrity check.
            c = sqlite3.connect("file:%s?mode=ro" % backup["path"], uri=True)
            try:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM sources")
                                 .fetchone()[0], 1)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM nodes")
                                 .fetchone()[0], 2)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM relationships")
                                 .fetchone()[0], 1)
                self.assertEqual(c.execute("PRAGMA integrity_check")
                                 .fetchone()[0], "ok")
            finally:
                c.close()

    def test_backups_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            seed_db(db)
            a = backup_database(db, os.path.join(tmp, "b"))
            b = backup_database(db, os.path.join(tmp, "b"))
            self.assertEqual(a["sha256"], b["sha256"])

    def test_backup_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            seed_db(db)
            backup_dir = os.path.join(tmp, "backups")
            r1 = import_plan(build_plan([node("a")], []), db,
                             backup_dir=backup_dir)
            r2 = import_plan(build_plan([node("b")], []), db,
                             backup_dir=backup_dir)
            self.assertTrue(r1.committed)
            self.assertTrue(r2.committed)
            self.assertNotEqual(r1.backup["path"], r2.backup["path"])
            self.assertTrue(os.path.isfile(r1.backup["path"]))
            self.assertTrue(os.path.isfile(r2.backup["path"]))

    def test_backup_database_helper(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            seed_db(db)
            info = backup_database(db, os.path.join(tmp, "backups"))
            self.assertTrue(os.path.isfile(info["path"]))
            self.assertEqual(info["sha256"], file_sha256(info["path"]))
            c = sqlite3.connect("file:%s?mode=ro" % info["path"], uri=True)
            try:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM sources")
                                 .fetchone()[0], 1)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM nodes")
                                 .fetchone()[0], 2)
            finally:
                c.close()


# -- refusal paths (database never written) --------------------------------

class RefusalTests(unittest.TestCase):
    def test_invalid_relationship_refused_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            seed_db(db)
            plan = build_plan([node("a")], [rel("GHOST", "extends", "a")])
            before = counts(db)
            result = import_plan(plan, db, backup_dir=os.path.join(tmp, "b"))
            self.assertFalse(result.committed)
            self.assertIsNone(result.backup)  # refused before backup/write
            self.assertEqual(counts(db), before)

    def test_malformed_provenance_refused_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            seed_db(db)
            plan = build_plan([node("a", "concept", provenance=[])], [])
            before = counts(db)
            result = import_plan(plan, db, backup_dir=os.path.join(tmp, "b"))
            self.assertFalse(result.committed)
            self.assertEqual(counts(db), before)
            codes = [e["code"] for e in result.errors]
            self.assertIn("missing_provenance", codes)

    def test_duplicate_within_plan_refused_before_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            seed_db(db)
            plan = build_plan([node("a"), node("a")], [])
            before = counts(db)
            result = import_plan(plan, db, backup_dir=os.path.join(tmp, "b"))
            self.assertFalse(result.committed)
            self.assertEqual(counts(db), before)
            codes = [e["code"] for e in result.errors]
            self.assertIn("duplicate_node_id", codes)

    def test_unresolved_relationship_target_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            seed_db(db)
            # target "MISSING" is dangling: allowed by dry-run, refused by import
            plan = build_plan([node("a")], [rel("a", "related_to", "MISSING")])
            before = counts(db)
            result = import_plan(plan, db, backup_dir=os.path.join(tmp, "b"))
            self.assertFalse(result.committed)
            self.assertIsNone(result.backup)
            self.assertEqual(counts(db), before)
            codes = [e["code"] for e in result.errors]
            self.assertIn("relationship_target_unresolved", codes)

    def test_missing_database_refused(self):
        result = import_plan(build_plan([node("a")], []),
                             "/nonexistent/db/knowledge.db")
        self.assertFalse(result.committed)
        codes = [e["code"] for e in result.errors]
        self.assertIn("database_missing", codes)


# -- atomic rollback (repository constraint failure) ------------------------

class RollbackTests(unittest.TestCase):
    def test_atomic_rollback_on_existing_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            seed_db(db)
            # node id collides with EXISTING database row; unique within plan.
            plan = build_plan(
                [node("existing-1", "concept"),
                 node("brand-new", "technology")],
                [rel("brand-new", "related_to", "existing-1")])
            before = counts(db)
            result = import_plan(plan, db, backup_dir=os.path.join(tmp, "b"))
            self.assertFalse(result.committed)
            self.assertTrue(result.rollback)
            self.assertEqual(counts(db), before)  # database unchanged
            # the source must NOT have been added
            repo = KnowledgeRepository(db)
            repo.initialize()
            try:
                self.assertEqual(repo.conn.execute(
                    "SELECT COUNT(*) FROM sources").fetchone()[0], 1)
                self.assertIsNone(repo.get_node("brand-new"))
            finally:
                repo.close()

    def test_failed_import_still_creates_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            seed_db(db)
            plan = build_plan([node("existing-1")], [])
            result = import_plan(plan, db, backup_dir=os.path.join(tmp, "b"))
            self.assertFalse(result.committed)
            self.assertTrue(result.rollback)
            self.assertIsNotNone(result.backup)
            self.assertTrue(os.path.isfile(result.backup["path"]))
            self.assertEqual(counts(db), {"sources": 1, "nodes": 2,
                                          "relationships": 1})


# -- CLI -------------------------------------------------------------------

class ImportCLITests(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run([sys.executable, "-m", "importing", *args],
                              cwd=_ROOT, capture_output=True, text=True)

    def test_import_command_refuses_without_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            seed_db(db)
            plan_path = os.path.join(tmp, "plan.json")
            with open(plan_path, "w") as f:
                json.dump(build_plan([node("a")], []), f)
            r = self._run("import", plan_path, "--db", db)
            self.assertEqual(r.returncode, 1)
            self.assertIn("--yes", r.stdout)
            self.assertEqual(counts(db), {"sources": 1, "nodes": 2,
                                          "relationships": 1})

    def test_import_command_refuses_invalid_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            seed_db(db)
            plan_path = os.path.join(tmp, "plan.json")
            with open(plan_path, "w") as f:
                json.dump(build_plan([node("a"), node("a")], []), f)
            r = self._run("import", plan_path, "--db", db, "--yes")
            self.assertEqual(r.returncode, 1)
            self.assertIn("duplicate_node_id", r.stdout)
            self.assertEqual(counts(db), {"sources": 1, "nodes": 2,
                                          "relationships": 1})

    def test_import_command_commits_with_yes(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "knowledge.db")
            seed_db(db)
            plan_path = os.path.join(tmp, "plan.json")
            with open(plan_path, "w") as f:
                json.dump(build_plan([node("a")], []), f)
            r = self._run("import", plan_path, "--db", db,
                          "--backup-dir", os.path.join(tmp, "backups"),
                          "--yes")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("RESULT: COMMITTED", r.stdout)
            self.assertEqual(counts(db), {"sources": 2, "nodes": 3,
                                          "relationships": 1})


if __name__ == "__main__":
    unittest.main()
