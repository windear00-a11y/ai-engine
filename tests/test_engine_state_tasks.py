"""Layer 6A tests: persistent task state schema + primitives on EngineState.

Covers (6A scope only — no execution, no planner, no TaskEngine):
* fresh engine_state.db initializes meta/tasks/task_steps
* initialization is idempotent and deterministic
* pre-Layer-6 EngineState DB migrates safely; journal/operations/audit intact
* task create/get with idempotent identical duplicates + conflict fail-closed
* guarded task status transitions and atomic task claiming (exactly-one-wins)
* write-ahead step primitives (pending->running->success/failed/skipped)
* JSON validation fails closed; deterministic serialization
* concurrency: two claimants, duplicate creates, mixed ops -> no corruption
* production database/knowledge.db is never touched

All state DBs are temporary; the production knowledge DB is only ever read
(invariant assertion).
"""

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..")))

from tools.permissions import EngineState
from tools.permissions.journal import ENGINE_STATE_SCHEMA_VERSION

PROD_KB = os.path.join(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))), "database", "knowledge.db")
PROD_KB_SHA = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"


def _tmp_db():
    return os.path.join(tempfile.mkdtemp(prefix="es_tasks_"), "engine_state.db")


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _tables(conn, names):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    present = {r[0] for r in rows}
    return present, names


class EngineStateTaskSchemaTests(unittest.TestCase):
    def test_fresh_init_creates_schema(self):
        db = _tmp_db()
        EngineState(db_path=db)
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            present, names = _tables(conn, ("meta", "tasks", "task_steps",
                                            "journal", "operations", "audit"))
            self.assertTrue(present.issuperset(names), sorted(set(names) - present))
            cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)")]
            for c in ("task_id", "task_json", "workspace_root", "status",
                      "status_reason", "owner_token", "created_at_epoch",
                      "updated_at_epoch", "finished_at_epoch", "result_json",
                      "planner_version", "proposal_ids_json"):
                self.assertIn(c, cols)
            scol = [r[1] for r in conn.execute("PRAGMA table_info(task_steps)")]
            for c in ("task_id", "step_id", "idx", "tool", "inputs_json",
                      "status", "result_json", "error", "operation_id",
                      "started_at_epoch", "finished_at_epoch", "duration"):
                self.assertIn(c, scol)
        finally:
            conn.close()

    def test_schema_version_is_deterministic_and_persisted(self):
        self.assertEqual(ENGINE_STATE_SCHEMA_VERSION, "2")
        db = _tmp_db()
        EngineState(db_path=db)
        conn = sqlite3.connect(db)
        try:
            row = conn.execute(
                "SELECT v FROM meta WHERE k='schema_version'").fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "2")
        finally:
            conn.close()

    def test_init_twice_is_safe(self):
        db = _tmp_db()
        EngineState(db_path=db)
        st = EngineState(db_path=db)
        self.assertTrue(st.integrity_check()["ok"])
        conn = sqlite3.connect(db)
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            self.assertGreaterEqual(count, 6)
        finally:
            conn.close()

    def test_pre_layer6_db_migrates_safely_and_keeps_rows(self):
        db = _tmp_db()
        st = EngineState(db_path=db)
        # Simulate an existing pre-Layer-6 EngineState DB: drop the 6A tables
        # and seed journal/operations/audit rows exactly as Phase 1B would.
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("DROP TABLE task_steps")
            conn.execute("DROP TABLE tasks")
            conn.execute("DROP TABLE meta")
            conn.execute(
                "INSERT INTO journal (operation_id, target_rel, tier, checksum, "
                "size_bytes, content, status, created_at_epoch, after_checksum, "
                "existed_before, snapshot_path) VALUES "
                "('op_old', 'src/a.py', 't1_reversible', 'c1', 3, "
                "x'616263', 'snapshot_stored', 1.0, NULL, 1, NULL)")
            conn.execute(
                "INSERT INTO operations (operation_id, domain, status, "
                "created_at_epoch, updated_at_epoch) VALUES "
                "('op_old', 'write', 'completed', 1.0, 1.0)")
            conn.execute(
                "INSERT INTO audit (operation_id, domain, target, permission, "
                "decision, approval_id, status, result, error, checksum_ref, "
                "created_at_epoch) VALUES "
                "('op_old', 'write', 'src/a.py', 'file.edit', 'require_approval', "
                "'op_old', 'approved', 'ok', NULL, NULL, 1.0)")
            conn.commit()
        finally:
            conn.close()
        # Re-run initialization on the migrated DB.
        st2 = EngineState(db_path=db)
        self.assertTrue(st2.integrity_check()["ok"])
        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row
        try:
            present, names = _tables(conn, ("meta", "tasks", "task_steps"))
            self.assertTrue(present.issuperset(names))
            j = conn.execute(
                "SELECT operation_id, target_rel, checksum, size_bytes FROM "
                "journal").fetchall()
            self.assertEqual(len(j), 1)
            self.assertEqual(j[0]["target_rel"], "src/a.py")
            self.assertEqual(j[0]["checksum"], "c1")
            op = conn.execute(
                "SELECT operation_id, domain, status FROM operations").fetchall()
            self.assertEqual(len(op), 1)
            self.assertEqual(op[0]["status"], "completed")
            au = conn.execute(
                "SELECT operation_id, decision, status FROM audit").fetchall()
            self.assertEqual(len(au), 1)
            self.assertEqual(au[0]["status"], "approved")
            # legacy journal columns still present after migration
            cols = [r[1] for r in conn.execute("PRAGMA table_info(journal)")]
            for c in ("after_checksum", "existed_before", "snapshot_path"):
                self.assertIn(c, cols)
            self.assertEqual(
                conn.execute(
                    "SELECT v FROM meta WHERE k='schema_version'").fetchone()[0],
                "2")
        finally:
            conn.close()

    def test_migration_does_not_touch_production_knowledge_db(self):
        before = _sha(PROD_KB)
        db = _tmp_db()
        st = EngineState(db_path=db)
        st.create_task("t-1", {"a": 1}, "/ws", planner_version="1")
        self.assertTrue(st.integrity_check()["ok"])
        self.assertEqual(_sha(PROD_KB), before)
        self.assertEqual(_sha(PROD_KB), PROD_KB_SHA)


class EngineStateTaskApiTests(unittest.TestCase):
    def setUp(self):
        self.db = _tmp_db()
        self.st = EngineState(db_path=self.db)
        self.st.create_task("t-1", {"a": 1}, "/ws", planner_version="1",
                            proposal_ids_json=["p1"])
        self.st.create_task("t-2", {"a": 2}, "/ws")

    def test_create_and_get_task(self):
        t = self.st.get_task("t-1")
        self.assertIsNotNone(t)
        self.assertEqual(t["task_id"], "t-1")
        self.assertEqual(json.loads(t["task_json"]), {"a": 1})
        self.assertEqual(t["workspace_root"], "/ws")
        self.assertEqual(t["status"], "planned")
        self.assertEqual(t["planner_version"], "1")
        self.assertEqual(json.loads(t["proposal_ids_json"]), ["p1"])
        self.assertIsNone(t["result_json"])
        self.assertIsNotNone(t["created_at_epoch"])

    def test_get_missing_task_returns_none(self):
        self.assertIsNone(self.st.get_task("nope"))

    def test_create_accepts_dict_and_canonicalizes(self):
        r = self.st.create_task("t-3", {"z": 1, "a": 2,
                                        "b": {"y": 0, "x": 1}}, "/ws")
        self.assertTrue(r["ok"] and r["created"])
        self.assertEqual(self.st.get_task("t-3")["task_json"],
                         json.dumps({"a": 2, "b": {"x": 1, "y": 0}, "z": 1},
                                    sort_keys=True))

    def test_duplicate_identical_create_is_idempotent(self):
        r = self.st.create_task("t-1", {"a": 1}, "/ws", planner_version="1",
                                proposal_ids_json=["p1"])
        self.assertTrue(r["ok"])
        self.assertFalse(r["created"])
        self.assertTrue(r["idempotent"])
        self.assertEqual(self.st.get_task("t-1")["task_json"],
                         '{"a": 1}')

    def test_conflicting_task_id_fails_closed(self):
        r = self.st.create_task("t-1", {"a": 999}, "/ws")
        self.assertFalse(r["ok"])
        self.assertIn("different task_json/workspace_root", r["error"])
        self.assertEqual(json.loads(self.st.get_task("t-1")["task_json"]),
                         {"a": 1})
        r2 = self.st.create_task("t-1", {"a": 1}, "/other/ws")
        self.assertFalse(r2["ok"])
        self.assertIn("different task_json/workspace_root", r2["error"])

    def test_malformed_json_fails_closed(self):
        r = self.st.create_task("t-bad", "this is {not json", "/ws")
        self.assertFalse(r["ok"])
        self.assertIn("not valid JSON", r["error"])
        r2 = self.st.create_task("t-2-bad", {"a": 2}, "/ws",
                                 proposal_ids_json="{nope")
        self.assertFalse(r2["ok"])
        r3 = self.st.update_task_result("t-2", "not-json")
        self.assertFalse(r3["ok"])
        r4 = self.st.ensure_task_step("t-2", "s1", 0, "file.read",
                                      "not-json")
        self.assertFalse(r4["ok"])

    def test_running_not_creatable_directly(self):
        r = self.st.create_task("t-run", {"a": 1}, "/ws", status="running")
        self.assertFalse(r["ok"])
        self.assertIn("claim_task", r["error"])

    def test_check_constraints_reject_bad_statuses(self):
        r = self.st.create_task("t-re", {"a": 1}, "/ws", status="cancelled")
        self.assertFalse(r["ok"])
        self.assertIn("invalid task status", r["error"])
        self.assertIsNone(self.st.get_task("t-re"))

    def test_update_task_result(self):
        r = self.st.update_task_result("t-1", {"status": "completed",
                                               "step_count": 2},
                                       planner_version="1")
        self.assertTrue(r["ok"] and r["updated"])
        t = self.st.get_task("t-1")
        self.assertEqual(json.loads(t["result_json"])["step_count"], 2)


class EngineStateTaskTransitionsTests(unittest.TestCase):
    def setUp(self):
        self.db = _tmp_db()
        self.st = EngineState(db_path=self.db)
        self.st.create_task("t", {"a": 1}, "/ws")

    def test_guarded_legal_sequence(self):
        r = self.st.update_task_status("t", "running", expected_status="planned")
        self.assertTrue(r["ok"] and r["updated"])
        r = self.st.update_task_status("t", "completed", expected_status="running")
        self.assertTrue(r["ok"] and r["updated"])
        t = self.st.get_task("t")
        self.assertEqual(t["status"], "completed")
        self.assertIsNotNone(t["finished_at_epoch"])

    def test_illegal_transition_fails_without_modification(self):
        r = self.st.update_task_status("t", "completed",
                                       expected_status="planned")
        self.assertFalse(r["ok"])
        self.assertIn("invalid transition", r["error"])
        self.assertEqual(self.st.get_task("t")["status"], "planned")
        r2 = self.st.update_task_status("t", "running",
                                        expected_status="completed")
        self.assertFalse(r2["ok"])
        self.assertEqual(self.st.get_task("t")["status"], "planned")

    def test_expected_status_mismatch_is_not_updated(self):
        # task is 'planned'; caller expects 'running' but the current row
        # does not match, so nothing is modified even though the transition
        # itself (running->completed) is legal.
        r = self.st.update_task_status("t", "completed",
                                       expected_status="running")
        self.assertTrue(r["ok"])
        self.assertFalse(r["updated"])
        self.assertEqual(self.st.get_task("t")["status"], "planned")

    def test_invalid_status_target_fails_closed(self):
        r = self.st.update_task_status("t", "bogus")
        self.assertFalse(r["ok"])

    def test_missing_task_fails_closed(self):
        r = self.st.update_task_status("missing", "completed",
                                       expected_status="running")
        self.assertFalse(r["ok"])
        self.assertFalse(r["updated"])


class EngineStateClaimTests(unittest.TestCase):
    def setUp(self):
        self.db = _tmp_db()
        self.st = EngineState(db_path=self.db)
        self.st.create_task("t", {"a": 1}, "/ws")

    def test_claim_requires_owner_token(self):
        r = self.st.claim_task("t", "")
        self.assertFalse(r["ok"])
        self.assertFalse(r["claimed"])
        r2 = self.st.claim_task("t", None)
        self.assertFalse(r2["ok"])
        self.assertEqual(self.st.get_task("t")["status"], "planned")

    def test_first_claimant_wins_second_fails(self):
        r1 = self.st.claim_task("t", "owner-a")
        self.assertTrue(r1["ok"])
        self.assertTrue(r1["claimed"])
        self.assertEqual(self.st.get_task("t")["status"], "running")
        self.assertEqual(self.st.get_task("t")["owner_token"], "owner-a")
        r2 = self.st.claim_task("t", "owner-b")
        self.assertTrue(r2["ok"])
        self.assertFalse(r2["claimed"])
        self.assertEqual(self.st.get_task("t")["owner_token"], "owner-a")

    def test_claim_missing_task_fails(self):
        r = self.st.claim_task("nope", "x")
        self.assertFalse(r["claimed"])

    def test_concurrent_claims_exactly_one_wins(self):
        self.st.create_task("tc", {"a": 1}, "/ws")
        n = 16
        with ThreadPoolExecutor(max_workers=n) as ex:
            results = list(ex.map(lambda _: self.st.claim_task("tc", "owner"),
                                  range(n)))
        claimed = [r for r in results if r["claimed"]]
        self.assertEqual(len(claimed), 1)
        self.assertEqual(self.st.get_task("tc")["status"], "running")
        self.assertTrue(self.st.integrity_check()["ok"])


class EngineStateStepTests(unittest.TestCase):
    def setUp(self):
        self.db = _tmp_db()
        self.st = EngineState(db_path=self.db)
        self.st.create_task("t", {"a": 1}, "/ws")

    def test_write_ahead_step_lifecycle(self):
        r = self.st.ensure_task_step("t", "s1", 0, "file.read", {"path": "a.py"})
        self.assertTrue(r["ok"] and r["created"])
        self.assertEqual(self.st.get_task_step("t", "s1")["status"], "pending")

        self.assertTrue(self.st.update_task_step_status(
            "t", "s1", "running", expected_status="pending")["updated"])
        self.assertTrue(self.st.update_step_result(
            "t", "s1", result_json={"content": "x"}, started_at_epoch=1.0,
            duration=0.5)["updated"])
        self.assertTrue(self.st.update_task_step_status(
            "t", "s1", "success", expected_status="running")["updated"])
        step = self.st.get_task_step("t", "s1")
        self.assertEqual(step["status"], "success")
        self.assertEqual(json.loads(step["result_json"]), {"content": "x"})
        self.assertIsNotNone(step["finished_at_epoch"])

    def test_step_failed_and_skipped(self):
        self.st.ensure_task_step("t", "f", 0, "file.read", {})
        self.st.update_task_step_status("t", "f", "running",
                                        expected_status="pending")
        self.assertTrue(self.st.update_task_step_status(
            "t", "f", "failed", expected_status="running")["updated"])
        self.assertTrue(self.st.update_step_result(
            "t", "f", error="boom")["updated"])
        self.assertEqual(self.st.get_task_step("t", "f")["error"], "boom")

        self.st.ensure_task_step("t", "k", 1, "project.test", {})
        self.st.update_task_step_status("t", "k", "running",
                                        expected_status="pending")
        self.assertTrue(self.st.update_task_step_status(
            "t", "k", "skipped", expected_status="running")["updated"])
        self.assertEqual(self.st.get_task_step("t", "k")["status"], "skipped")

    def test_step_requires_parent_task_fk(self):
        r = self.st.ensure_task_step("missing-t", "s1", 0, "file.read", {})
        self.assertFalse(r["ok"])
        self.assertIn("not insertable", r["error"])

    def test_identical_step_duplicate_is_idempotent(self):
        self.st.ensure_task_step("t", "s1", 0, "file.read", {"path": "a.py"})
        r = self.st.ensure_task_step("t", "s1", 0, "file.read", {"path": "a.py"})
        self.assertTrue(r["ok"])
        self.assertFalse(r["created"])
        self.assertTrue(r["idempotent"])

    def test_conflicting_step_duplicate_fails_closed(self):
        self.st.ensure_task_step("t", "s1", 0, "file.read", {"path": "a.py"})
        r = self.st.ensure_task_step("t", "s1", 0, "file.edit", {"path": "a.py"})
        self.assertFalse(r["ok"])
        self.assertIn("conflicting", r["error"])

    def test_step_transition_guards(self):
        self.st.ensure_task_step("t", "s1", 0, "file.read", {})
        # pending->success is illegal
        r = self.st.update_task_step_status("t", "s1", "success",
                                            expected_status="pending")
        self.assertFalse(r["ok"])
        self.assertIn("invalid step transition", r["error"])
        self.assertEqual(self.st.get_task_step("t", "s1")["status"], "pending")
        # running->running illegal
        self.st.update_task_step_status("t", "s1", "running",
                                        expected_status="pending")
        r = self.st.update_task_step_status("t", "s1", "running",
                                            expected_status="running")
        self.assertFalse(r["ok"])
        self.assertIn("invalid step transition", r["error"])
        # success->failed illegal
        self.st.update_task_step_status("t", "s1", "success",
                                        expected_status="running")
        r = self.st.update_task_step_status("t", "s1", "failed",
                                            expected_status="success")
        self.assertFalse(r["ok"])

    def test_list_steps_ordered_by_idx(self):
        for i, tool in enumerate(["project.inspect", "file.read", "file.diff"]):
            self.assertTrue(self.st.ensure_task_step(
                "t", f"s{i}", i, tool, {})["ok"])
        steps = self.st.list_task_steps("t")
        self.assertEqual([s["idx"] for s in steps], [0, 1, 2])
        self.assertEqual([s["tool"] for s in steps],
                         ["project.inspect", "file.read", "file.diff"])
        self.assertEqual(self.st.list_task_steps("missing"), [])

    def test_attach_operation(self):
        self.st.ensure_task_step("t", "s1", 0, "file.edit", {})
        r = self.st.attach_operation("t", "s1", "op_123")
        self.assertTrue(r["ok"] and r["updated"])
        self.assertEqual(self.st.get_task_step("t", "s1")["operation_id"],
                         "op_123")
        r2 = self.st.attach_operation("t", "s1", "")
        self.assertFalse(r2["ok"])
        self.assertEqual(self.st.get_task_step("t", "s1")["operation_id"],
                         "op_123")


class EngineStateConcurrencyTests(unittest.TestCase):
    def test_concurrent_identical_creates_no_corruption(self):
        db = _tmp_db()
        st = EngineState(db_path=db)
        task_json = {"steps": [{"id": "s", "tool": "file.read",
                                "inputs": {"path": "a.py"}}]}

        def create(_):
            return st.create_task("same", task_json, "/ws")

        n = 8
        with ThreadPoolExecutor(max_workers=n) as ex:
            results = list(ex.map(create, range(n)))
        created = [r for r in results if r["created"]]
        idempotent = [r for r in results if (not r["created"]) and r["idempotent"]]
        self.assertEqual(len(created), 1)
        self.assertEqual(len(idempotent), n - 1)
        t = st.get_task("same")
        self.assertEqual(json.loads(t["task_json"]), task_json)
        self.assertTrue(st.integrity_check()["ok"])

    def test_concurrent_guarded_transition_single_winner(self):
        db = _tmp_db()
        st = EngineState(db_path=db)
        st.create_task("race", {"a": 1}, "/ws")

        def fn(_):
            return st.update_task_status("race", "running",
                                         expected_status="planned")

        n = 8
        with ThreadPoolExecutor(max_workers=n) as ex:
            results = list(ex.map(fn, range(n)))
        winners = [r for r in results if r["updated"]]
        self.assertEqual(len(winners), 1)
        self.assertEqual(st.get_task("race")["status"], "running")
        self.assertTrue(st.integrity_check()["ok"])

    def test_mixed_rapid_operations_no_corruption(self):
        db = _tmp_db()
        st = EngineState(db_path=db)

        def worker(i):
            tid = f"task-{i}"
            st.create_task(tid, {"n": i}, f"/ws-{i % 2}")
            st.ensure_task_step(tid, "s0", 0, "file.read", {"path": "a.py"})
            st.update_task_step_status(tid, "s0", "running",
                                       expected_status="pending")
            st.update_step_result(tid, "s0", result_json={"content": i})
            st.update_task_step_status(tid, "s0", "success",
                                       expected_status="running")
            st.update_task_result(tid, {"n": i, "done": True})
            st.update_task_status(tid, "running", expected_status="planned")

        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(worker, range(24)))
        self.assertTrue(st.integrity_check()["ok"])
        for i in range(24):
            t = st.get_task(f"task-{i}")
            self.assertEqual(t["status"], "running")
            s = st.get_task_step(f"task-{i}", "s0")
            self.assertEqual(s["status"], "success")
            self.assertEqual(json.loads(s["result_json"])["content"], i)


class EngineStateListTasksTests(unittest.TestCase):
    """Layer 6F: public read-only task enumeration."""

    def test_empty_state(self):
        st = EngineState(db_path=_tmp_db())
        self.assertEqual(st.list_tasks(), [])
        self.assertEqual(st.list_tasks(status="running"), [])

    def test_mixed_statuses_ordered_and_filtered(self):
        db = _tmp_db()
        st = EngineState(db_path=db)
        st.create_task("b", {"id": "b"}, "/ws")
        st.create_task("a", {"id": "a"}, "/ws")
        st.create_task("c", {"id": "c"}, "/ws")
        st.update_task_status("c", "running", expected_status="planned")
        st.claim_task("a", "owner-1")
        all_rows = st.list_tasks()
        self.assertEqual([r["task_id"] for r in all_rows],
                         ["a", "b", "c"])  # deterministic asc
        run_rows = st.list_tasks(status="running")
        self.assertEqual([r["task_id"] for r in run_rows], ["a", "c"])

    def test_unknown_status_fails_closed(self):
        st = EngineState(db_path=_tmp_db())
        self.assertEqual(st.list_tasks(status="bogus"), [])

    def test_summary_fields_match_rows(self):
        db = _tmp_db()
        st = EngineState(db_path=db)
        st.create_task("t1", {"id": "t1"}, "/ws", planner_version="9")
        st.claim_task("t1", "owner-z")
        row = st.get_task("t1")
        listing = st.list_tasks(status="running")[0]
        self.assertEqual(listing["task_id"], "t1")
        self.assertEqual(listing["status"], row["status"])
        self.assertEqual(listing["owner_token"], "owner-z")
        self.assertEqual(listing["planner_version"], row["planner_version"])
        self.assertEqual(listing["created_at_epoch"], row["created_at_epoch"])

    def test_read_only_no_mutation(self):
        db = _tmp_db()
        st = EngineState(db_path=db)
        st.create_task("t1", {"id": "t1"}, "/ws")
        before = (dict(st.get_task("t1")),
                  _sha(db))
        st.list_tasks()
        st.list_tasks(status="planned")
        after = (dict(st.get_task("t1")),
                 _sha(db))
        self.assertEqual(after, before)


if __name__ == "__main__":
    unittest.main()