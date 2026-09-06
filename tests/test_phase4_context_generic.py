"""Phase 4 — Generic Context.

Tests for:
  * 8 generic dimensions (environment/project/source/actor/spatial/social/affective/temporal)
  * Deterministic context_id (temporal excluded)
  * Validation (no uncontrolled dump)
  * Store persistence + migration + per-project isolation
  * Capture/Memory integration with generic context (no coding assumptions)
  * Preservation of legacy API and existing boundaries
"""

import os
import sys
import tempfile
import unittest
import sqlite3
import time

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


class GenericContextSchemaTests(unittest.TestCase):
    def test_generic_dimensions_support(self):
        from intelligence.context.schema import ContextSnapshot
        snap = ContextSnapshot.build(
            environment={"os": "linux", "device": "test"},
            project={"project_id": "default", "vocabulary_id": "diary_v1"},
            source={"adapter": "manual", "payload_hash": "abc"},
            actor={"user_id": "alice"},
            spatial={"location": "home"},
            social={"with": "bob"},
            affective={"mood": "calm"},
            temporal={"captured_at_epoch": 1234567890.0},
            captured_at_epoch=1234567890.0,
        )
        self.assertEqual(snap.environment["os"], "linux")
        self.assertEqual(snap.project["project_id"], "default")
        self.assertEqual(snap.source["adapter"], "manual")
        self.assertEqual(snap.actor["user_id"], "alice")
        self.assertEqual(snap.spatial["location"], "home")
        self.assertEqual(snap.social["with"], "bob")
        self.assertEqual(snap.affective["mood"], "calm")
        self.assertTrue(snap.context_id.startswith("ctx_"))

    def test_temporal_excluded_from_id(self):
        from intelligence.context.schema import ContextSnapshot
        base = dict(environment={"os": "linux"}, project={"project_id": "default"}, source={"adapter": "manual"}, actor={}, spatial={}, social={}, affective={})
        snap1 = ContextSnapshot.build(environment=base["environment"], project=base["project"], source=base["source"], actor=base["actor"], spatial=base["spatial"], social=base["social"], affective=base["affective"], temporal={"captured_at_epoch": 1000.0}, captured_at_epoch=1000.0)
        snap2 = ContextSnapshot.build(environment=base["environment"], project=base["project"], source=base["source"], actor=base["actor"], spatial=base["spatial"], social=base["social"], affective=base["affective"], temporal={"captured_at_epoch": 9999.0}, captured_at_epoch=9999.0)
        self.assertEqual(snap1.context_id, snap2.context_id)
        # Different non-temporal should change id
        snap3 = ContextSnapshot.build(environment={"os": "darwin"}, project=base["project"], source=base["source"], temporal={"captured_at_epoch": 1000.0}, captured_at_epoch=1000.0)
        self.assertNotEqual(snap1.context_id, snap3.context_id)

    def test_actor_spatial_social_affective_affect_id(self):
        from intelligence.context.schema import ContextSnapshot
        base = dict(environment={"os": "linux"}, project={"project_id": "default"}, source={"adapter": "manual"}, temporal={})
        s1 = ContextSnapshot.build(environment=base["environment"], project=base["project"], source=base["source"], actor={"user_id": "alice"}, temporal=base["temporal"])
        s2 = ContextSnapshot.build(environment=base["environment"], project=base["project"], source=base["source"], actor={"user_id": "bob"}, temporal=base["temporal"])
        self.assertNotEqual(s1.context_id, s2.context_id)
        s3 = ContextSnapshot.build(environment=base["environment"], project=base["project"], source=base["source"], spatial={"location": "home"}, temporal=base["temporal"])
        s4 = ContextSnapshot.build(environment=base["environment"], project=base["project"], source=base["source"], spatial={"location": "office"}, temporal=base["temporal"])
        self.assertNotEqual(s3.context_id, s4.context_id)

    def test_no_uncontrolled_dump(self):
        from intelligence.context.schema import ContextSnapshot
        # Unexpected top-level kwarg should fail
        with self.assertRaises(ValueError):
            ContextSnapshot.build(environment={}, project={}, source={}, temporal={}, unexpected="dump")
        # Non-dict dimension should fail
        with self.assertRaises(ValueError):
            ContextSnapshot.build(environment="not a dict", project={}, source={}, temporal={})
        # Non-string keys should fail
        with self.assertRaises(ValueError):
            ContextSnapshot.build(environment={123: "bad"}, project={}, source={}, temporal={})

    def test_legacy_compat_still_works(self):
        from intelligence.context.schema import ContextSnapshot
        # Old 4-dim call via system/project/task
        snap = ContextSnapshot.build(system={"os": "linux"}, project={"language": "python"}, task={"type": "bug_fix"}, temporal={"captured_at_epoch": 1.0}, captured_at_epoch=1.0)
        self.assertTrue(snap.context_id.startswith("ctx_"))
        self.assertEqual(snap.system, snap.environment)
        self.assertEqual(snap.task, snap.source)
        # Old derive still consistent via generic
        from intelligence.context.schema import derive_context_id
        cid = derive_context_id(environment={"os": "linux"}, project={"language": "python"}, source={"type": "bug_fix"})
        self.assertEqual(snap.context_id, cid)

    def test_deterministic_across_calls(self):
        from intelligence.context.schema import ContextSnapshot
        kwargs = dict(environment={"os": "linux"}, project={"project_id": "default"}, source={"adapter": "manual"}, actor={"user_id": "alice"}, temporal={})
        a = ContextSnapshot.build(**kwargs, captured_at_epoch=1.0)
        b = ContextSnapshot.build(**kwargs, captured_at_epoch=9999.0)
        self.assertEqual(a.context_id, b.context_id)


class GenericContextStoreTests(unittest.TestCase):
    def test_generic_persist_and_retrieve(self):
        from intelligence.context.schema import ContextSnapshot
        from intelligence.context.store import ContextStore
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "ctx.db")
            store = ContextStore(db_path=db)
            snap = ContextSnapshot.build(
                environment={"os": "linux"}, project={"project_id": "default"},
                source={"adapter": "manual"}, actor={"user_id": "alice"},
                spatial={"location": "home"}, social={"with": "bob"}, affective={"mood": "calm"},
                temporal={"captured_at_epoch": 123.0}, captured_at_epoch=123.0)
            store.save(snap)
            got = store.get(snap.context_id)
            self.assertIsNotNone(got)
            self.assertEqual(got.environment, snap.environment)
            self.assertEqual(got.actor, snap.actor)
            self.assertEqual(got.spatial, snap.spatial)
            self.assertEqual(got.affective, snap.affective)
            self.assertEqual(got.context_id, snap.context_id)
            store.close()

    def test_store_migration_from_legacy(self):
        # Create old DB with 4 columns, then open with new store and verify migration
        from intelligence.context.schema import ContextSnapshot
        from intelligence.context.store import ContextStore
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "ctx.db")
            # Create legacy DB manually with old schema
            con = sqlite3.connect(db)
            con.execute("""
                CREATE TABLE context_snapshots (
                    context_id TEXT PRIMARY KEY,
                    system_json TEXT, project_json TEXT, task_json TEXT, temporal_json TEXT, captured_at_epoch REAL
                )
            """)
            snap_old = ContextSnapshot.build(system={"os": "linux"}, project={"project_id": "old"}, task={"type": "bug_fix"}, temporal={"captured_at_epoch": 1.0}, captured_at_epoch=1.0)
            con.execute("INSERT INTO context_snapshots (context_id, system_json, project_json, task_json, temporal_json, captured_at_epoch) VALUES (?,?,?,?,?,?)",
                        (snap_old.context_id, '{"os": "linux"}', '{"project_id": "old"}', '{"type": "bug_fix"}', '{"captured_at_epoch": 1.0}', 1.0))
            con.commit()
            con.close()
            # Now open with new store (should migrate)
            store = ContextStore(db_path=db)
            # Should still read old row via fallback
            got = store.get(snap_old.context_id)
            self.assertIsNotNone(got)
            self.assertEqual(got.environment.get("os"), "linux")
            # New save should work with new columns
            snap_new = ContextSnapshot.build(environment={"os": "darwin"}, project={"project_id": "new"}, source={"adapter": "manual"}, temporal={}, captured_at_epoch=2.0)
            store.save(snap_new)
            self.assertIsNotNone(store.get(snap_new.context_id))
            store.close()

    def test_per_project_isolation(self):
        from intelligence.context.schema import ContextSnapshot
        from intelligence.context.store import ContextStore
        from ai_engine.paths import get_context_db
        with tempfile.TemporaryDirectory() as tmp:
            db_a = get_context_db("default", tmp)
            db_b = get_context_db("research", tmp)
            store_a = ContextStore(db_path=db_a)
            store_b = ContextStore(db_path=db_b)
            snap_a = ContextSnapshot.build(environment={"os": "linux"}, project={"project_id": "default"}, source={"adapter": "manual"}, temporal={}, captured_at_epoch=1.0)
            snap_b = ContextSnapshot.build(environment={"os": "linux"}, project={"project_id": "research"}, source={"adapter": "manual"}, temporal={}, captured_at_epoch=1.0)
            store_a.save(snap_a)
            store_b.save(snap_b)
            self.assertIsNotNone(store_a.get(snap_a.context_id))
            self.assertIsNone(store_a.get(snap_b.context_id))
            self.assertIsNotNone(store_b.get(snap_b.context_id))
            self.assertIsNone(store_b.get(snap_a.context_id))
            store_a.close()
            store_b.close()


class CaptureGenericContextTests(unittest.TestCase):
    def test_capture_uses_generic_dimensions(self):
        from ai_engine.capture import run_capture
        with tempfile.TemporaryDirectory() as tmp:
            res = run_capture("generic context test", project_id="default", data_root=tmp, vocabulary_id="diary_v1", context_hints={"actor": {"user_id": "alice"}, "spatial": {"location": "home"}, "affective": {"mood": "calm"}})
            self.assertTrue(res["ok"], res)
            ctx_id = res["context_id"]
            from ai_engine.paths import get_context_db
            from intelligence.context.store import ContextStore
            store = ContextStore(db_path=get_context_db("default", tmp))
            snap = store.get(ctx_id)
            self.assertIsNotNone(snap)
            self.assertEqual(snap.actor.get("user_id"), "alice")
            self.assertEqual(snap.spatial.get("location"), "home")
            self.assertEqual(snap.affective.get("mood"), "calm")
            self.assertEqual(snap.project.get("project_id"), "default")
            self.assertEqual(snap.source.get("adapter"), "manual")
            store.close()

    def test_capture_context_hints_validation(self):
        from ai_engine.capture import run_capture
        with tempfile.TemporaryDirectory() as tmp:
            # Unknown key should fail controlled
            res = run_capture("test", project_id="default", data_root=tmp, vocabulary_id="diary_v1", context_hints={"unknown_dump": "bad"})
            self.assertFalse(res["ok"])
            self.assertEqual(res["code"], "invalid_argument")
            self.assertIn("not allowed", res["error"])
            # Non-dict actor should fail
            res2 = run_capture("test2", project_id="default", data_root=tmp, vocabulary_id="diary_v1", context_hints={"actor": "not a dict"})
            self.assertFalse(res2["ok"])
            # Valid flat hints should work
            res3 = run_capture("test3", project_id="default", data_root=tmp, vocabulary_id="diary_v1", context_hints={"user_id": "bob", "location": "office"})
            self.assertTrue(res3["ok"], res3)
            from ai_engine.paths import get_context_db
            from intelligence.context.store import ContextStore
            store = ContextStore(db_path=get_context_db("default", tmp))
            snap = store.get(res3["context_id"])
            self.assertEqual(snap.actor.get("user_id"), "bob")
            self.assertEqual(snap.spatial.get("location"), "office")
            store.close()

    def test_capture_temporal_excluded(self):
        from ai_engine.capture import run_capture
        import time
        with tempfile.TemporaryDirectory() as tmp:
            # Two captures with same generic context but different times should have same context_id
            # We control this by using same project/source/actor and different temporal (time)
            # Since run_capture uses time.time() for temporal, we can't directly control, but we can test via schema
            from intelligence.context.schema import ContextSnapshot
            snap1 = ContextSnapshot.build(environment={"os": "linux"}, project={"project_id": "default"}, source={"adapter": "manual"}, temporal={"captured_at_epoch": 1000.0}, captured_at_epoch=1000.0)
            snap2 = ContextSnapshot.build(environment={"os": "linux"}, project={"project_id": "default"}, source={"adapter": "manual"}, temporal={"captured_at_epoch": 9999.0}, captured_at_epoch=9999.0)
            self.assertEqual(snap1.context_id, snap2.context_id)

    def test_memory_still_generic(self):
        from ai_engine.memory import Memory
        with tempfile.TemporaryDirectory() as tmp:
            mem = Memory(project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            r1 = mem.remember(text="generic memory test", type="fact")
            self.assertTrue(r1["ok"], r1)
            # Recall with generic context
            out = mem.recall(query="generic memory", context={"context_id": r1["context_id"]})
            self.assertTrue(out["ok"], out)
            self.assertGreaterEqual(len(out["result"]["knowledge"]), 1)

    def test_no_coding_assumption_in_capture(self):
        from ai_engine.capture import run_capture
        with tempfile.TemporaryDirectory() as tmp:
            # Capture without project_root language detection should still succeed and be generic
            res = run_capture("non-coding diary entry about walk", project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            self.assertTrue(res["ok"], res)
            # Context should be generic, not contain language/framework
            from ai_engine.paths import get_context_db
            from intelligence.context.store import ContextStore
            store = ContextStore(db_path=get_context_db("default", tmp))
            snap = store.get(res["context_id"])
            # Should have environment/project/source/actor etc., but not forced language
            self.assertIn("os", snap.environment)
            self.assertEqual(snap.project.get("project_id"), "default")
            # Should not have been forced into coding task type
            self.assertNotIn("language", snap.project)  # unless project_root was given
            store.close()

    def test_activity_still_separate(self):
        from ai_engine.capture import run_capture
        with tempfile.TemporaryDirectory() as tmp:
            res = run_capture("activity isolation", project_id="default", data_root=tmp, vocabulary_id="diary_v1")
            self.assertTrue(res["ok"], res)
            from ai_engine.paths import get_activity_db
            act_db = get_activity_db("default", tmp)
            self.assertTrue(os.path.exists(act_db))
            eng_db = os.path.join(tmp, "default", "engine_state.db")
            if os.path.exists(eng_db):
                con = sqlite3.connect(f"file:{eng_db}?mode=ro", uri=True)
                tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                self.assertNotIn("activities", tables)
                con.close()

    def test_invariants(self):
        from api.contract import CONTRACT_VERSION, OPERATIONS
        from tools.permissions.policy import HARD_WRITE_INVARIANTS
        import intelligence
        self.assertEqual(CONTRACT_VERSION, "1")
        self.assertEqual(tuple(OPERATIONS), ("search", "get", "related", "follow", "provenance", "inspect"))
        self.assertEqual(list(HARD_WRITE_INVARIANTS), [("database/knowledge.db", "blocked"), ("database/knowledge.db.backup", "blocked")])
        self.assertEqual(intelligence.__version__, "0")
        self.assertEqual(_sha256(LEGACY_DB), EXPECTED_SHA)
        con = sqlite3.connect(f"file:{LEGACY_DB}?mode=ro", uri=True)
        self.assertEqual(con.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        con.close()


if __name__ == "__main__":
    unittest.main()
