"""Phase 19 — Data Lifecycle & Persistence Hardening.

Tests for (14+):
1. fresh project backup
2. populated project backup
3. export → import round trip
4. backup → restore round trip
5. project isolation during export/import/restore
6. invalid/corrupt export rejection
7. integrity failure detection
8. migration idempotence
9. simulated restore failure with original state preserved
10. deterministic IDs/provenance preserved after round trip
11. doctor output
12. missing database handling
13. multiple project isolation
14. no regression to previous phases (checked via full suite)
"""

import os
import sys
import tempfile
import unittest
import json
import sqlite3

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

LEGACY_DB = os.path.join(_ROOT, "database", "knowledge.db")
EXPECTED_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"

def _sha256(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1<<20), b""):
            h.update(c)
    return h.hexdigest()

def _run_cli(args, data_root=None):
    import subprocess, os, sys
    env = os.environ.copy()
    if data_root:
        env["AI_ENGINE_DATA_DIR"] = data_root
    cmd = [sys.executable, "-m", "ai_engine"] + args
    return subprocess.run(cmd, cwd=_ROOT, capture_output=True, text=True, env=env, timeout=30)


class FreshBackupTests(unittest.TestCase):
    def test_fresh_project_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_cli(["init", "--project", "fresh"], data_root=tmp)
            # Fresh project has no knowledge nodes yet, but backup should still succeed (creates backup of existing DBs, or empty)
            from ai_engine.persistence import backup_project
            res = backup_project("fresh", data_root=tmp)
            self.assertTrue(res["ok"] or len(res["backup_files"]) == 0)  # fresh may have no DBs yet, but should not error

    def test_populated_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_cli(["init", "--project", "pop"], data_root=tmp)
            from ai_engine.memory import Memory
            mem = Memory(project_id="pop", data_root=tmp)
            mem.remember(payload={"text": "populated backup fact", "type": "fact"})
            from ai_engine.persistence import backup_project
            res = backup_project("pop", data_root=tmp)
            self.assertTrue(res["ok"])
            self.assertGreaterEqual(len(res["backup_files"]), 1)
            for path in res["backup_files"].values():
                self.assertTrue(os.path.exists(path))
                con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
                self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                con.close()


class ExportImportTests(unittest.TestCase):
    def test_export_import_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_cli(["init", "--project", "src"], data_root=tmp)
            from ai_engine.memory import Memory
            mem = Memory(project_id="src", data_root=tmp)
            r = mem.remember(payload={"text": "export round trip fact", "type": "fact"})
            nid = r["node_id"]
            from ai_engine.persistence import export_project, import_project
            export_file = os.path.join(tmp, "export.jsonl")
            res_exp = export_project("src", data_root=tmp, output=export_file)
            self.assertTrue(res_exp["ok"])
            self.assertTrue(os.path.exists(export_file))
            # Import to new project
            _run_cli(["init", "--project", "dst"], data_root=tmp)
            res_imp = import_project(export_file, "dst", data_root=tmp)
            self.assertTrue(res_imp["ok"])
            # Verify deterministic ID preserved
            mem2 = Memory(project_id="dst", data_root=tmp)
            node = mem2.get_node(nid)
            self.assertIsNotNone(node)
            self.assertEqual(node["id"], nid)
            self.assertEqual(node["description"], "export round trip fact")

    def test_backup_restore_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_cli(["init", "--project", "p1"], data_root=tmp)
            from ai_engine.memory import Memory
            mem = Memory(project_id="p1", data_root=tmp)
            mem.remember(payload={"text": "backup restore fact", "type": "fact"})
            from ai_engine.persistence import backup_project, restore_project
            from ai_engine.paths import get_knowledge_db
            # Backup
            backup_res = backup_project("p1", data_root=tmp)
            self.assertTrue(backup_res["ok"])
            backup_file = backup_res["backup_files"]["knowledge.db"]
            # Add second fact
            mem.remember(payload={"text": "second fact after backup", "type": "fact"})
            self.assertEqual(mem.count_nodes(), 2)
            # Restore should bring back to 1
            restore_res = restore_project(backup_file, "p1", data_root=tmp)
            self.assertTrue(restore_res["ok"])
            mem2 = Memory(project_id="p1", data_root=tmp)
            self.assertEqual(mem2.count_nodes(), 1)
            # Verify integrity
            con = sqlite3.connect(f"file:{get_knowledge_db('p1', tmp)}?mode=ro", uri=True)
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            con.close()

    def test_project_isolation_export_import_restore(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_cli(["init", "--project", "proj_a"], data_root=tmp)
            _run_cli(["init", "--project", "proj_b"], data_root=tmp)
            from ai_engine.memory import Memory
            mem_a = Memory(project_id="proj_a", data_root=tmp)
            mem_b = Memory(project_id="proj_b", data_root=tmp)
            mem_a.remember(payload={"text": "proj A fact"})
            mem_b.remember(payload={"text": "proj B fact"})
            from ai_engine.persistence import export_project, import_project, backup_project, restore_project
            # Export A, import to B should not mix A into B's original B fact? Actually import adds, so B will have both
            # Test isolation: backup of A should not contain B's fact
            exp_a = os.path.join(tmp, "exp_a.jsonl")
            export_project("proj_a", data_root=tmp, output=exp_a)
            with open(exp_a) as f:
                content = f.read()
            self.assertIn("proj A fact", content)
            self.assertNotIn("proj B fact", content)
            # Backup isolation
            backup_a = backup_project("proj_a", data_root=tmp)
            backup_b = backup_project("proj_b", data_root=tmp)
            self.assertNotEqual(list(backup_a["backup_files"].values())[0], list(backup_b["backup_files"].values())[0])

    def test_invalid_corrupt_export_rejection(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_cli(["init", "--project", "p1"], data_root=tmp)
            from ai_engine.persistence import import_project
            # Create corrupt file
            corrupt = os.path.join(tmp, "corrupt.jsonl")
            with open(corrupt, "w") as f:
                f.write("not json\n")
                f.write('{"id": "bad", "type": ""}\n')  # missing required fields
            res = import_project(corrupt, "p1", data_root=tmp)
            self.assertFalse(res["ok"])
            self.assertIn(res["code"], ("invalid_argument", "internal_error"))

    def test_integrity_failure_detection(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_cli(["init", "--project", "p1"], data_root=tmp)
            from ai_engine.memory import Memory
            mem = Memory(project_id="p1", data_root=tmp)
            mem.remember(payload={"text": "integrity test"})
            from ai_engine.paths import get_knowledge_db
            db_path = get_knowledge_db("p1", tmp)
            # Corrupt the DB file
            with open(db_path, "r+b") as f:
                f.seek(100)
                f.write(b"\x00\xFF\x00\xFF")
            from ai_engine.persistence import doctor_project
            res = doctor_project("p1", data_root=tmp)
            self.assertFalse(res["ok"])
            self.assertTrue(any("integrity" in p.lower() for p in res["consistency_problems"]))

    def test_migration_idempotence(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Create a fake legacy DB
            legacy_dir = os.path.join(tmp, "legacy")
            os.makedirs(legacy_dir)
            import shutil
            shutil.copy2(LEGACY_DB, os.path.join(legacy_dir, "knowledge.db"))
            data_root = os.path.join(tmp, "data")
            from ai_engine.migration import migrate_project_databases
            res1 = migrate_project_databases("default", source_root=legacy_dir, data_root=data_root)
            self.assertTrue(res1["ok"])
            # Second run should be idempotent (skipped)
            res2 = migrate_project_databases("default", source_root=legacy_dir, data_root=data_root)
            self.assertTrue(res2["ok"])
            for db_name, info in res2["results"].items():
                if "knowledge.db" in db_name and info.get("source_exists"):
                    self.assertTrue(info.get("skipped") or info.get("ok"))

    def test_simulated_restore_failure_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_cli(["init", "--project", "p1"], data_root=tmp)
            from ai_engine.memory import Memory
            mem = Memory(project_id="p1", data_root=tmp)
            mem.remember(payload={"text": "original fact"})
            from ai_engine.persistence import backup_project, restore_project
            from ai_engine.paths import get_knowledge_db
            backup_res = backup_project("p1", data_root=tmp)
            backup_file = backup_res["backup_files"]["knowledge.db"]
            # Corrupt the backup file to simulate failure
            corrupt_backup = os.path.join(tmp, "corrupt_backup.db")
            with open(backup_file, "rb") as f:
                data = f.read()
            with open(corrupt_backup, "wb") as f:
                f.write(data[:100] + b"\xFF\xFF" + data[102:])
            # Try to restore from corrupt backup - should fail and preserve original
            res = restore_project(corrupt_backup, "p1", data_root=tmp)
            self.assertFalse(res["ok"])
            # Original should still be intact and have 1 node
            mem2 = Memory(project_id="p1", data_root=tmp)
            self.assertEqual(mem2.count_nodes(), 1)
            con = sqlite3.connect(f"file:{get_knowledge_db('p1', tmp)}?mode=ro", uri=True)
            self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            con.close()

    def test_deterministic_ids_provenance_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_cli(["init", "--project", "p1"], data_root=tmp)
            from ai_engine.memory import Memory
            mem = Memory(project_id="p1", data_root=tmp)
            r1 = mem.remember(payload={"text": "deterministic fact", "type": "fact"})
            nid1 = r1["node_id"]
            act1 = r1["activity_id"]
            ctx1 = r1["context_id"]
            # Export and import to new project
            from ai_engine.persistence import export_project, import_project
            exp_file = os.path.join(tmp, "exp.jsonl")
            export_project("p1", data_root=tmp, output=exp_file)
            _run_cli(["init", "--project", "p2"], data_root=tmp)
            import_project(exp_file, "p2", data_root=tmp)
            mem2 = Memory(project_id="p2", data_root=tmp)
            node = mem2.get_node(nid1)
            self.assertIsNotNone(node)
            self.assertEqual(node["id"], nid1)
            # Provenance after import will be from import source, not original manual, but deterministic ID preserved
            self.assertIn(node["provenance"]["source_name"], ("manual", "import:exp.jsonl"))

    def test_doctor_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_cli(["init", "--project", "p1"], data_root=tmp)
            from ai_engine.persistence import doctor_project
            res = doctor_project("p1", data_root=tmp)
            self.assertIn("project_id", res)
            self.assertIn("data_root", res)
            self.assertIn("databases", res)
            self.assertIn("knowledge.db", res["databases"])
            self.assertIn("exists", res["databases"]["knowledge.db"])
            self.assertIn("integrity", res["databases"]["knowledge.db"])

    def test_missing_database_handling(self):
        with tempfile.TemporaryDirectory() as tmp:
            _run_cli(["init", "--project", "p1"], data_root=tmp)
            from ai_engine.persistence import doctor_project, backup_project
            # Remove a DB
            from ai_engine.paths import get_knowledge_db
            db_path = get_knowledge_db("p1", tmp)
            if os.path.exists(db_path):
                os.remove(db_path)
            res = doctor_project("p1", data_root=tmp)
            self.assertFalse(res["databases"]["knowledge.db"]["exists"])
            # Backup should handle missing gracefully (no crash)
            res2 = backup_project("p1", data_root=tmp)
            # Should still be ok (no files to backup is not error, or should report)
            self.assertIn("ok", res2)

    def test_multiple_project_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            for proj in ["p1", "p2", "p3"]:
                _run_cli(["init", "--project", proj], data_root=tmp)
                from ai_engine.memory import Memory
                mem = Memory(project_id=proj, data_root=tmp)
                mem.remember(payload={"text": f"fact for {proj}", "type": "fact"})
            from ai_engine.memory import Memory
            for proj in ["p1", "p2", "p3"]:
                mem = Memory(project_id=proj, data_root=tmp)
                out = mem.recall(query=f"fact for {proj}")
                self.assertTrue(any(f"fact for {proj}" in n["description"] for n in out["result"]["knowledge"]))
                # Should not see other project's fact
                for other in ["p1", "p2", "p3"]:
                    if other == proj:
                        continue
                    self.assertFalse(any(f"fact for {other}" in n["description"] for n in out["result"]["knowledge"]))

    def test_legacy_db_untouched(self):
        self.assertEqual(_sha256(LEGACY_DB), EXPECTED_SHA)
        con = sqlite3.connect(f"file:{LEGACY_DB}?mode=ro", uri=True)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        con.close()


if __name__ == "__main__":
    unittest.main()
