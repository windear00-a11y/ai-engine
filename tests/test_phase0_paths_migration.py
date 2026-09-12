"""Phase 0 — Product data-directory and project-isolation foundation.

Tests for ai_engine/paths.py and ai_engine/migration.py.

Covers:
  * migration preserves the source database byte-for-byte
  * contract v1 remains unchanged
  * HARD_WRITE_INVARIANTS remain unchanged
  * path resolution with AI_ENGINE_DATA_DIR / XDG_DATA_HOME / default
  * project paths cannot escape data root
  * two project IDs resolve to isolated directories
  * migration is idempotent
  * source database remains unchanged after migration
  * migrated database passes integrity_check

All tests are stdlib-only, use :memory: or temp dirs, never require or
create the committed database, never touch tools/permissions or
intelligence/* behavior.
"""

import hashlib
import os
import sqlite3
import sys
import tempfile
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

EXPECTED_CONTRACT_VERSION = "1"
EXPECTED_OPERATIONS = ("search", "get", "related", "follow", "provenance", "inspect")
EXPECTED_HARD_WRITE_INVARIANTS = [
    ("database/knowledge.db", "blocked"),
    ("database/knowledge.db.backup", "blocked"),
]


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class InvariantTests(unittest.TestCase):
    def test_contract_v1_unchanged(self):
        from api.contract import CONTRACT_VERSION, OPERATIONS
        self.assertEqual(CONTRACT_VERSION, EXPECTED_CONTRACT_VERSION)
        self.assertEqual(tuple(OPERATIONS), EXPECTED_OPERATIONS)

    def test_hard_write_invariants_unchanged(self):
        from tools.permissions.policy import HARD_WRITE_INVARIANTS
        self.assertEqual(list(HARD_WRITE_INVARIANTS), EXPECTED_HARD_WRITE_INVARIANTS)


class PathsResolutionTests(unittest.TestCase):
    def setUp(self):
        # Snapshot env
        self._orig_ai = os.environ.get("AI_ENGINE_DATA_DIR")
        self._orig_xdg = os.environ.get("XDG_DATA_HOME")
        self._orig_home = os.environ.get("HOME")

    def tearDown(self):
        # Restore
        for k, v in (("AI_ENGINE_DATA_DIR", self._orig_ai), ("XDG_DATA_HOME", self._orig_xdg), ("HOME", self._orig_home)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        # Ensure cached values don't leak: reimport
        import importlib
        if "ai_engine.paths" in sys.modules:
            importlib.reload(sys.modules["ai_engine.paths"])

    def test_ai_engine_data_dir_takes_precedence(self):
        from ai_engine import paths
        with tempfile.TemporaryDirectory() as tmp:
            custom = os.path.join(tmp, "custom-data")
            xdg = os.path.join(tmp, "xdg")
            os.environ["AI_ENGINE_DATA_DIR"] = custom
            os.environ["XDG_DATA_HOME"] = xdg
            import importlib
            importlib.reload(paths)
            self.assertEqual(paths.get_data_root(), os.path.abspath(custom))

    def test_xdg_fallback(self):
        from ai_engine import paths
        with tempfile.TemporaryDirectory() as tmp:
            xdg = os.path.join(tmp, "xdg-home")
            os.environ.pop("AI_ENGINE_DATA_DIR", None)
            os.environ["XDG_DATA_HOME"] = xdg
            import importlib
            importlib.reload(paths)
            self.assertEqual(paths.get_data_root(), os.path.join(os.path.abspath(xdg), "ai-engine"))

    def test_default_home_fallback(self):
        from ai_engine import paths
        with tempfile.TemporaryDirectory() as tmp:
            os.environ.pop("AI_ENGINE_DATA_DIR", None)
            os.environ.pop("XDG_DATA_HOME", None)
            os.environ["HOME"] = tmp
            import importlib
            importlib.reload(paths)
            expected = os.path.join(tmp, ".ai-engine")
            self.assertEqual(paths.get_data_root(), expected)

    def test_empty_env_treated_as_unset(self):
        from ai_engine import paths
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["AI_ENGINE_DATA_DIR"] = "   "
            os.environ["XDG_DATA_HOME"] = ""
            os.environ["HOME"] = tmp
            import importlib
            importlib.reload(paths)
            self.assertEqual(paths.get_data_root(), os.path.join(tmp, ".ai-engine"))

    def test_helpers_produce_expected_paths(self):
        from ai_engine import paths
        with tempfile.TemporaryDirectory() as tmp:
            dr = tmp
            pid = "default"
            self.assertEqual(paths.get_knowledge_db(pid, dr), os.path.join(dr, pid, "knowledge.db"))
            self.assertEqual(paths.get_context_db(pid, dr), os.path.join(dr, pid, "context.db"))
            self.assertEqual(paths.get_evidence_db(pid, dr), os.path.join(dr, pid, "evidence.db"))
            self.assertEqual(paths.get_experience_db(pid, dr), os.path.join(dr, pid, "experience.db"))
            self.assertEqual(paths.get_engine_state_db(pid, dr), os.path.join(dr, pid, "engine_state.db"))
            self.assertEqual(paths.get_snapshots_dir(pid, dr), os.path.join(dr, pid, "snapshots"))
            self.assertEqual(paths.get_backups_dir(pid, dr), os.path.join(dr, pid, "backups"))
            self.assertEqual(paths.get_projects_registry(dr), os.path.join(dr, "projects.json"))
            self.assertEqual(paths.get_users_registry(dr), os.path.join(dr, "users.json"))

    def test_project_paths_cannot_escape_data_root(self):
        from ai_engine import paths
        with tempfile.TemporaryDirectory() as tmp:
            dr = tmp
            for bad in ["../evil", "..", "a/../b", "a/b", "a\\b", "", "DEFAULT"]:
                with self.assertRaises(ValueError, msg=bad):
                    paths.get_project_dir(bad, dr)
            # Also test that absolute path traversal via project_id with dot segments is rejected
            with self.assertRaises(ValueError):
                paths.get_project_dir("../../etc", dr)
            # Ensure _ensure_inside_data_root catches traversal via symlink trick (basic)
            # Use a valid id but manipulate data_root to be inside project path -> not allowed
            # This is more about get_project_dir never escaping: valid ids always stay inside
            good = paths.get_project_dir("myproj", dr)
            self.assertTrue(os.path.commonpath([os.path.realpath(dr), os.path.realpath(good)]) == os.path.realpath(dr))

    def test_two_projects_isolated(self):
        from ai_engine import paths
        with tempfile.TemporaryDirectory() as tmp:
            dr = tmp
            p1 = paths.get_knowledge_db("default", dr)
            p2 = paths.get_knowledge_db("research", dr)
            self.assertNotEqual(p1, p2)
            self.assertNotEqual(os.path.dirname(p1), os.path.dirname(p2))
            self.assertTrue(p1.endswith(os.path.join("default", "knowledge.db")))
            self.assertTrue(p2.endswith(os.path.join("research", "knowledge.db")))

    def test_known_dbs_list(self):
        from ai_engine import paths
        self.assertIn("knowledge.db", set(paths.list_known_dbs()))
        self.assertIn("context.db", set(paths.list_known_dbs()))
        self.assertIn("evidence.db", set(paths.list_known_dbs()))
        self.assertIn("experience.db", set(paths.list_known_dbs()))
        self.assertIn("engine_state.db", set(paths.list_known_dbs()))

    def test_unknown_db_rejected(self):
        from ai_engine import paths
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                paths.get_db_path("default", "evil.db", tmp)


class ProjectRegistryTests(unittest.TestCase):
    def test_ensure_default_project_idempotent(self):
        from ai_engine import paths
        with tempfile.TemporaryDirectory() as tmp:
            reg1 = paths.ensure_default_project(tmp)
            self.assertIn("default", {p["project_id"] for p in reg1["projects"]})
            reg2 = paths.ensure_default_project(tmp)
            self.assertEqual(len([p for p in reg2["projects"] if p["project_id"] == "default"]), 1)
            # File exists
            self.assertTrue(os.path.exists(paths.get_projects_registry(tmp)))

    def test_create_project_and_collision(self):
        from ai_engine import paths
        with tempfile.TemporaryDirectory() as tmp:
            paths.ensure_default_project(tmp)
            entry = paths.create_project("research", display_name="Research", data_root=tmp)
            self.assertEqual(entry["project_id"], "research")
            # collision fails closed
            with self.assertRaises(ValueError):
                paths.create_project("research", data_root=tmp)
            # invalid id fails closed
            with self.assertRaises(ValueError):
                paths.create_project("../evil", data_root=tmp)
            # ensure dirs created
            self.assertTrue(os.path.isdir(paths.get_project_dir("research", tmp)))
            self.assertTrue(os.path.isdir(paths.get_snapshots_dir("research", tmp)))
            self.assertTrue(os.path.isdir(paths.get_backups_dir("research", tmp)))

    def test_list_and_get(self):
        from ai_engine import paths
        with tempfile.TemporaryDirectory() as tmp:
            paths.ensure_default_project(tmp)
            paths.create_project("proj2", data_root=tmp)
            lst = paths.list_projects(tmp)
            self.assertEqual({p["project_id"] for p in lst}, {"default", "proj2"})
            self.assertIsNotNone(paths.get_project_entry("default", tmp))
            self.assertIsNone(paths.get_project_entry("nonexistent", tmp))


class MigrationTests(unittest.TestCase):
    def test_migrate_database_idempotent_and_preserves_source(self):
        from ai_engine.migration import migrate_database
        with tempfile.TemporaryDirectory() as tmp:
            # Create a tiny legacy DB as source
            src_dir = os.path.join(tmp, "source")
            os.makedirs(src_dir)
            src = os.path.join(src_dir, "knowledge.db")
            # Build minimal source DB via repository
            sys.path.insert(0, _ROOT)
            from retrieval.repository import KnowledgeRepository
            repo = KnowledgeRepository(src)
            repo.initialize()
            sid = repo.add_source("test-src")
            repo.add_node("n1", "concept", "N1", "desc", source_id=sid)
            repo.close()
            src_sha_before = _sha256(src)

            dest_dir = os.path.join(tmp, "dest")
            os.makedirs(dest_dir)
            dest = os.path.join(dest_dir, "knowledge.db")

            # First migration should copy
            res1 = migrate_database(src, dest)
            self.assertTrue(res1["ok"], res1)
            self.assertFalse(res1.get("skipped"))
            self.assertTrue(os.path.exists(dest))
            src_sha_after_first = _sha256(src)
            self.assertEqual(src_sha_before, src_sha_after_first, "source must be unchanged after first copy")
            # Integrity ok
            con = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            con.close()

            # Second migration should be idempotent (skipped)
            res2 = migrate_database(src, dest)
            self.assertTrue(res2["ok"], res2)
            self.assertTrue(res2.get("skipped"))
            self.assertEqual(_sha256(src), src_sha_before)

            # Dest still integrity ok
            con = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            con.close()

            # Dest collision with different content should fail closed
            # Corrupt dest by adding a node (different SHA)
            repo2 = KnowledgeRepository(dest)
            # repo2 already has n1; add n2
            sid2 = repo2.add_source("extra")
            repo2.add_node("n2", "concept", "N2", "desc2", source_id=sid2)
            repo2.close()
            # Now migrate again should error (dest exists with different content)
            res3 = migrate_database(src, dest)
            self.assertFalse(res3["ok"])
            self.assertIn("different content", res3.get("error", ""))

    def test_migrate_database_preserves_source_byte_for_byte(self):
        from ai_engine.migration import migrate_database
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.db")
            # Build a small legacy-schema source DB via the repository
            from retrieval.repository import KnowledgeRepository
            repo = KnowledgeRepository(src)
            repo.initialize()
            sid = repo.add_source("src-src", version="1.0")
            repo.add_node("s1", "concept", "S1", "desc", source_id=sid)
            repo.add_node("s2", "concept", "S2", "desc", source_id=sid)
            repo.close()
            sha_before = _sha256(src)
            # counts before
            con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            cnt_before = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            con.close()
            dest = os.path.join(tmp, "dest.db")
            res = migrate_database(src, dest)
            self.assertTrue(res["ok"], res)
            self.assertEqual(_sha256(src), sha_before, "source unchanged after migration")
            # Dest must be logically identical (counts and integrity), byte SHA may differ due to sqlite3.backup page layout
            con = sqlite3.connect(f"file:{dest}?mode=ro", uri=True)
            cnt_dest = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
            con.close()
            self.assertEqual(cnt_dest, cnt_before, "dest must be logically identical to source")
            # Integrity
            for p in [src, dest]:
                con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                con.close()

    def test_migrate_database_symlink_dest_rejected(self):
        from ai_engine.migration import migrate_database
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.db")
            from retrieval.repository import KnowledgeRepository
            repo = KnowledgeRepository(src)
            repo.initialize()
            repo.close()
            dest = os.path.join(tmp, "dest_link.db")
            os.symlink(src, dest)
            res = migrate_database(src, dest)
            self.assertFalse(res["ok"])
            self.assertIn("symlink", res.get("error", ""))

    def test_migrate_project_databases_idempotent(self):
        from ai_engine.migration import migrate_project_databases, get_migration_status
        with tempfile.TemporaryDirectory() as tmp:
            src_root = os.path.join(tmp, "legacy")
            os.makedirs(src_root)
            # Build a tiny legacy-schema knowledge.db via the repository
            from retrieval.repository import KnowledgeRepository
            repo = KnowledgeRepository(os.path.join(src_root, "knowledge.db"))
            repo.initialize()
            sid0 = repo.add_source("legacy-src", version="1.0")
            repo.add_node("lg1", "concept", "LG1", "desc", source_id=sid0)
            repo.close()
            # Also create a tiny context.db
            ctx_path = os.path.join(src_root, "context.db")
            con = sqlite3.connect(ctx_path)
            con.execute("CREATE TABLE context_snapshots (context_id TEXT PRIMARY KEY, system_json TEXT, project_json TEXT, task_json TEXT, temporal_json TEXT, captured_at_epoch REAL)")
            con.execute("INSERT INTO context_snapshots VALUES ('ctx_test','{}','{}','{}','{}',0)")
            con.commit()
            con.close()
            ctx_sha_before = _sha256(ctx_path)
            legacy_knowledge_sha_before = _sha256(os.path.join(src_root, "knowledge.db"))

            data_root = os.path.join(tmp, "data")
            # First migrate
            res1 = migrate_project_databases("default", source_root=src_root, data_root=data_root)
            self.assertTrue(res1["ok"], res1)
            # Check dest exists and integrity
            from ai_engine.paths import get_knowledge_db, get_context_db
            k_dest = get_knowledge_db("default", data_root)
            c_dest = get_context_db("default", data_root)
            for p in [k_dest, c_dest]:
                self.assertTrue(os.path.exists(p))
                con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                con.close()
            # Source unchanged
            self.assertEqual(_sha256(os.path.join(src_root, "knowledge.db")), legacy_knowledge_sha_before)
            self.assertEqual(_sha256(ctx_path), ctx_sha_before)
            # Status read-only
            status = get_migration_status("default", source_root=src_root, data_root=data_root)
            self.assertTrue(status["dbs"]["knowledge.db"]["dest_exists"])
            self.assertTrue(status["dbs"]["context.db"]["dest_exists"])

            # Second migrate should be idempotent (all skipped or ok)
            res2 = migrate_project_databases("default", source_root=src_root, data_root=data_root)
            self.assertTrue(res2["ok"], res2)
            for db, r in res2["results"].items():
                if r.get("note") == "source not present":
                    continue
                # knowledge and context should be skipped (already migrated)
                if db in ("knowledge.db", "context.db"):
                    self.assertTrue(r.get("skipped") or r.get("ok"), r)

    def test_create_compat_symlink_safety(self):
        from ai_engine.migration import create_compat_symlink
        with tempfile.TemporaryDirectory() as tmp:
            data_root = os.path.join(tmp, "data")
            legacy_path = os.path.join(tmp, "legacy_knowledge.db")
            # When legacy exists as real file, should refuse
            with open(legacy_path, "wb") as f:
                f.write(b"real file")
            res = create_compat_symlink("default", legacy_path=legacy_path, data_root=data_root)
            self.assertFalse(res["ok"])
            self.assertIn("real file", res.get("error", ""))
            self.assertTrue(os.path.exists(legacy_path))
            self.assertFalse(os.path.islink(legacy_path))
            # When legacy does not exist, should create symlink
            os.remove(legacy_path)
            # Ensure dest exists so symlink target exists (create dummy dest)
            from ai_engine.paths import get_knowledge_db, ensure_default_project
            ensure_default_project(data_root)
            dest = get_knowledge_db("default", data_root)
            # create dummy dest file
            open(dest, "wb").close()
            res2 = create_compat_symlink("default", legacy_path=legacy_path, data_root=data_root)
            self.assertTrue(res2["ok"], res2)
            self.assertTrue(os.path.islink(legacy_path))
            # Second call should be idempotent
            res3 = create_compat_symlink("default", legacy_path=legacy_path, data_root=data_root)
            self.assertTrue(res3["ok"])
            self.assertTrue(res3.get("skipped"))

    def test_tools_permissions_and_intelligence_not_modified(self):
        # Ensure we did not accidentally modify those modules' behavior by importing paths/migration
        # (smoke: they still import and their invariants hold)
        from tools.permissions.policy import HARD_WRITE_INVARIANTS as hwi
        self.assertEqual(list(hwi), EXPECTED_HARD_WRITE_INVARIANTS)
        from api.contract import CONTRACT_VERSION
        self.assertEqual(CONTRACT_VERSION, EXPECTED_CONTRACT_VERSION)
        import intelligence
        self.assertEqual(intelligence.__version__, "0")


if __name__ == "__main__":
    unittest.main()
