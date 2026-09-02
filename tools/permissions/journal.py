"""Engine state store: journal + audit tables in a single SQLite DB.

``database/engine_state.db`` (configurable) holds:
* ``journal`` -- append-only snapshot records capturing pre-write state so a
  later milestone can implement rollback. Each record is the before-image of
  one file write: path, checksum, bytes(optional), and the operation that
  caused it.
* ``audit``  -- append-only deterministic event log of every operation,
  decision, approval, and outcome.

The production ``database/knowledge.db`` is NEVER touched by this module.

Timestamps are stored for audit/human inspection but are NOT used in any
decision or in any deterministic id/checksum.

Connections are short-lived, transactional, and always closed.
"""

import hashlib
import json
import os
import sqlite3
import time

DEFAULT_STATE_DB = None


def _default_state_db():
    global DEFAULT_STATE_DB
    if DEFAULT_STATE_DB is None:
        root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))))
        DEFAULT_STATE_DB = os.path.join(root, "database", "engine_state.db")
    return DEFAULT_STATE_DB


_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL,
    target_rel TEXT NOT NULL,
    tier TEXT NOT NULL,
    checksum TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    content BLOB,
    status TEXT NOT NULL,
    created_at_epoch REAL,
    after_checksum TEXT,
    existed_before INTEGER NOT NULL DEFAULT 1,
    snapshot_path TEXT
);
CREATE INDEX IF NOT EXISTS idx_journal_operation
    ON journal(operation_id);

CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    domain TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at_epoch REAL,
    updated_at_epoch REAL
);

CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    target TEXT NOT NULL,
    permission TEXT NOT NULL,
    decision TEXT NOT NULL,
    approval_id TEXT,
    status TEXT NOT NULL,
    result TEXT,
    error TEXT,
    checksum_ref TEXT,
    created_at_epoch REAL
);
CREATE INDEX IF NOT EXISTS idx_audit_operation
    ON audit(operation_id);
"""

# ============================================================================
# Layer 6A — persistent task state (additive to EngineState)
# ============================================================================

# Deterministic schema version stored in ``meta``. Not a timestamp: identical
# across runs and machines for a given implementation level.
ENGINE_STATE_SCHEMA_VERSION = "2"

# Statuses currently supported. "cancelled" is intentionally NOT added: no
# abort/ownership mechanism exists yet (see Layer 6 discovery report sections
# D/G/O). Anything outside these sets fails closed.
TASK_STATUSES = ("planned", "running", "completed", "failed",
                 "invalid", "rolled_back")
TASK_TERMINAL_STATUSES = ("completed", "failed", "invalid", "rolled_back")

TASK_STEP_STATUSES = ("pending", "running", "success", "failed",
                      "skipped", "planned")

# Explicit guarded transition tables. Only these are allowed; every other
# status change is rejected without touching the row.
TASK_TRANSITIONS = {
    ("planned", "running"),
    ("running", "completed"),
    ("running", "failed"),
    ("running", "invalid"),
    ("running", "rolled_back"),
}

TASK_STEP_TRANSITIONS = {
    ("pending", "running"),
    ("running", "success"),
    ("running", "failed"),
    ("running", "skipped"),
}

_TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT PRIMARY KEY,
    task_json TEXT NOT NULL,
    workspace_root TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('planned','running','completed','failed','invalid','rolled_back')),
    status_reason TEXT,
    owner_token TEXT,
    created_at_epoch REAL,
    updated_at_epoch REAL,
    finished_at_epoch REAL,
    result_json TEXT,
    planner_version TEXT,
    proposal_ids_json TEXT
);
CREATE TABLE IF NOT EXISTS task_steps (
    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
    step_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    tool TEXT NOT NULL,
    inputs_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','running','success','failed','skipped','planned')),
    result_json TEXT,
    error TEXT,
    operation_id TEXT,
    started_at_epoch REAL,
    finished_at_epoch REAL,
    duration REAL,
    PRIMARY KEY (task_id, step_id)
);
"""


def deterministic_id(prefix, *parts):
    """Deterministic operation/approval id from joined stable parts.

    Never uses randomness or time. The same inputs always yield the same id
    so audits are reproducible.
    """
    joined = "::".join(str(p) for p in parts)
    digest = hashlib.sha256(joined.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{digest}"


def checksum_bytes(data):
    """SHA-256 hex of bytes (deterministic)."""
    return hashlib.sha256(data).hexdigest()


class EngineState:
    """Thin transactional wrapper around the engine state DB.

    Snapshot storage is hybrid (Phase 1B): small before-images live in the
    SQLite ``journal.content`` BLOB, large ones in a filesystem snapshot
    directory referenced by ``journal.snapshot_path``. The cut-over threshold
    is configurable (``hybrid_threshold`` bytes; default 1 MiB) and was chosen
    from a measured comparison: below ~1 MiB the SQLite BLOB cost is flat and
    simpler, above it the filesystem is consistently faster.
    """

    HYBRID_THRESHOLD_DEFAULT = 1048576  # 1 MiB

    def __init__(self, db_path=None, snapshot_dir=None,
                 hybrid_threshold=None):
        self.db_path = db_path or _default_state_db()
        if snapshot_dir is None:
            snapshot_dir = os.path.join(os.path.dirname(self.db_path),
                                        "snapshots")
        self.snapshot_dir = snapshot_dir
        self.hybrid_threshold = (hybrid_threshold
                                 if hybrid_threshold is not None
                                 else self.HYBRID_THRESHOLD_DEFAULT)
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def _init_schema(self):
        parent = os.path.dirname(self.db_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            # Lightweight idempotent migration for pre-existing state DBs that
            # predate Phase 1B (add Phase 1B columns if absent).
            self._migrate_journal_columns(conn)
            self._ensure_task_tables(conn)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def _ensure_task_tables(conn):
        """Idempotent Layer 6A schema initialization + schema_version bump.

        CREATE TABLE IF NOT EXISTS is safe to run on a fresh DB, an existing
        pre-Layer-6 DB, and a DB already at the current version. The existing
        journal/operations/audit tables are never altered here (only the
        pre-existing journal column migration in ``_migrate_journal_columns``
        touches them).
        """
        conn.executescript(_TASK_SCHEMA)
        conn.execute(
            "INSERT INTO meta (k, v) VALUES ('schema_version', ?) "
            "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
            (ENGINE_STATE_SCHEMA_VERSION,))

    @staticmethod
    def _migrate_journal_columns(conn):
        cols = {row["name"] for row in conn.execute(
            "PRAGMA table_info(journal)")}
        additions = {
            "after_checksum": "after_checksum TEXT",
            "existed_before": "existed_before INTEGER NOT NULL DEFAULT 1",
            "snapshot_path": "snapshot_path TEXT",
        }
        for name, ddl in additions.items():
            if name not in cols:
                conn.execute(f"ALTER TABLE journal ADD COLUMN {ddl}")

    # -- journal -----------------------------------------------------------

    def add_journal(self, operation_id, target_rel, tier, content,
                    status="snapshot_stored", after_checksum=None,
                    existed_before=None, snapshot_path=None):
        """Persist a before-image snapshot for an approved write.

        ``content`` may be None to represent a new file (existed_before=0).
        Returns the journal record dict, or a structured error dict when
        content cannot be snapshotted (fail-closed; caller must not write).
        """
        if existed_before is None:
            existed_before = 1 if content is not None else 0
        if content is None:
            data = b""
            is_null = True
        else:
            try:
                data = content if isinstance(content, bytes) else content.encode(
                    "utf-8")
            except (AttributeError, UnicodeEncodeError):
                return {"ok": False, "error": "snapshot content not serializable"}
            is_null = False
        size = len(data)
        digest = "" if is_null else checksum_bytes(data)

        # Hybrid storage decision: keep small before-images in the BLOB; spill
        # large ones to the snapshot directory. The snapshot file is written
        # BEFORE the DB insert so a failure cannot leave a row without data;
        # an insert failure removes the orphan file (best effort).
        blob = None
        snapshot_path = None
        if not is_null and size >= self.hybrid_threshold:
            try:
                os.makedirs(self.snapshot_dir, exist_ok=True)
                name = ("snap_%s_%s_%s.bin"
                        % (operation_id, digest[:12],
                           os.urandom(4).hex()))
                abs_snap = os.path.join(self.snapshot_dir, name)
                with open(abs_snap, "wb") as f:
                    f.write(data)
                snapshot_path = name
            except Exception:
                return {"ok": False,
                        "error": "snapshot spill to filesystem failed"}
        elif not is_null:
            blob = sqlite3.Binary(data)

        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO journal (operation_id, target_rel, tier, "
                "checksum, size_bytes, content, status, created_at_epoch, "
                "after_checksum, existed_before, snapshot_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (operation_id, target_rel, tier, digest, size,
                 blob, status, time.time(), after_checksum, int(existed_before),
                 snapshot_path))
            conn.commit()
            seq = cur.lastrowid
            return {"ok": True, "seq": seq, "operation_id": operation_id,
                    "target_rel": target_rel, "tier": tier,
                    "checksum": digest, "size_bytes": size, "status": status,
                    "existed_before": int(existed_before)}
        except Exception:
            if snapshot_path and not is_null:
                try:
                    os.remove(os.path.join(self.snapshot_dir, snapshot_path))
                except Exception:
                    pass
            raise
        finally:
            conn.close()

    def snapshot_bytes(self, row):
        """Read a snapshot back from BLOB or filesystem (hybrid storage)."""
        content = row.get("content")
        if content is not None:
            return bytes(content)
        snap_rel = row.get("snapshot_path")
        if snap_rel:
            with open(os.path.join(self.snapshot_dir, snap_rel), "rb") as f:
                return f.read()
        return b""

    def journal_latest_for(self, target_rel):
        """Return the most recent journal record for a target (rollback ref)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM journal WHERE target_rel = ? "
                "ORDER BY seq DESC LIMIT 1", (target_rel,)).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def list_for_operation(self, operation_id):
        """All journal rows for an operation group (multi-file rollback)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM journal WHERE operation_id = ? "
                "ORDER BY seq ASC", (operation_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update_journal_after(self, operation_id, target_rel, after_checksum):
        """Record the post-write checksum for a snapshot row."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE journal SET after_checksum = ?, status = 'executing' "
                "WHERE operation_id = ? AND target_rel = ?",
                (after_checksum, operation_id, target_rel))
            conn.commit()
            return {"ok": cur.rowcount > 0}
        finally:
            conn.close()

    def set_journal_status(self, seq, status):
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE journal SET status = ? WHERE seq = ?",
                (status, seq))
            conn.commit()
            return {"ok": cur.rowcount > 0}
        finally:
            conn.close()

    def set_journal_checksum(self, seq, checksum, size_bytes):
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE journal SET checksum = ?, size_bytes = ? WHERE seq = ?",
                (checksum, size_bytes, seq))
            conn.commit()
            return {"ok": True}
        finally:
            conn.close()

    # -- operations (rollback state machine) -------------------------------

    def ensure_operation(self, operation_id, domain, status="prepared"):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT status FROM operations WHERE operation_id = ?",
                (operation_id,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO operations (operation_id, domain, status, "
                    "created_at_epoch, updated_at_epoch) VALUES (?, ?, ?, ?, ?)",
                    (operation_id, domain, status, time.time(), time.time()))
                conn.commit()
                return {"ok": True, "created": True, "status": status}
            return {"ok": True, "created": False, "status": row["status"]}
        finally:
            conn.close()

    def get_operation(self, operation_id):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM operations WHERE operation_id = ?",
                (operation_id,)).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def set_operation_status(self, operation_id, status):
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE operations SET status = ?, updated_at_epoch = ? "
                "WHERE operation_id = ?",
                (status, time.time(), operation_id))
            conn.commit()
            return {"ok": True, "operation_id": operation_id, "status": status}
        finally:
            conn.close()

    # -- audit -------------------------------------------------------------

    def add_audit(self, operation_id, domain, target, permission, decision,
                  status, approval_id=None, result=None, error=None,
                  checksum_ref=None):
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO audit (operation_id, domain, target, permission, "
                "decision, approval_id, status, result, error, checksum_ref, "
                "created_at_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (operation_id, domain, target, permission, decision,
                 approval_id, status,
                 result if result is None else str(result)[:4000],
                 error, checksum_ref, time.time()))
            conn.commit()
            return {"ok": True, "seq": cur.lastrowid, "operation_id":
                    operation_id, "decision": decision, "status": status}
        finally:
            conn.close()

    def audit_records(self, operation_id=None, limit=1000):
        conn = self._connect()
        try:
            if operation_id is not None:
                rows = conn.execute(
                    "SELECT * FROM audit WHERE operation_id = ? "
                    "ORDER BY seq", (operation_id,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit ORDER BY seq DESC LIMIT ?",
                    (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def audit_count(self):
        conn = self._connect()
        try:
            row = conn.execute("SELECT COUNT(*) AS c FROM audit").fetchone()
            return int(row["c"])
        finally:
            conn.close()

    def integrity_check(self):
        conn = self._connect()
        try:
            row = conn.execute("PRAGMA integrity_check").fetchone()
            return {"ok": row[0] == "ok", "result": row[0]}
        finally:
            conn.close()

    # -- Layer 6A: task persistence primitives ------------------------------

    @staticmethod
    def _canonical_json(value, field):
        """Return (canonical_json_string, error) or (None, None).

        Deterministic serialization: ``sort_keys`` + ``ensure_ascii=False``,
        mirroring the api-layer convention. Accepts an already-serialized JSON
        string (validated) or a JSON-serializable object. Malformed JSON fails
        closed with a clear error; never stores partial/raw values.
        """
        if value is None:
            return None, None
        if isinstance(value, str):
            try:
                obj = json.loads(value)
            except Exception:
                return None, f"{field} is not valid JSON"
        else:
            obj = value
        try:
            return json.dumps(obj, ensure_ascii=False, sort_keys=True), None
        except Exception:
            return None, f"{field} is not JSON-serializable"

    def create_task(self, task_id, task_json, workspace_root, status="planned",
                    status_reason=None, owner_token=None,
                    planner_version=None, proposal_ids_json=None):
        """Persist a task (idempotent for identical data, fail-closed otherwise).

        Deterministic identity: ``task_id`` is supplied by the caller (the
        planner's ``stable_id``), never generated here. Timestamps are
        audit metadata only.

        * identical existing row (same task_json + workspace_root) -> idempotent
        * same task_id with different task_json/workspace_root -> fail closed
        """
        if not isinstance(task_id, str) or not task_id:
            return {"ok": False, "error": "task_id must be a non-empty string"}
        if not isinstance(workspace_root, str) or not workspace_root:
            return {"ok": False,
                    "error": "workspace_root must be a non-empty string"}
        if status not in TASK_STATUSES:
            return {"ok": False,
                    "error": f"invalid task status: {status!r}"}
        if status == "running":
            return {"ok": False,
                    "error": "status 'running' must be reached via claim_task"}
        tj, tj_err = self._canonical_json(task_json, "task_json")
        if tj_err:
            return {"ok": False, "error": tj_err}
        pid, pid_err = self._canonical_json(proposal_ids_json,
                                            "proposal_ids_json")
        if pid_err:
            return {"ok": False, "error": pid_err}
        now = time.time()
        conn = self._connect()
        try:
            prev = conn.execute(
                "SELECT task_json, workspace_root FROM tasks "
                "WHERE task_id = ?", (task_id,)).fetchone()
            if prev is not None:
                if prev["task_json"] == tj and \
                        prev["workspace_root"] == workspace_root:
                    return {"ok": True, "created": False, "idempotent": True,
                            "task_id": task_id, "status": status}
                return {"ok": False, "created": False,
                        "error": f"task_id {task_id!r} already exists with "
                                 f"different task_json/workspace_root"}
            try:
                conn.execute(
                    "INSERT INTO tasks (task_id, task_json, workspace_root, "
                    "status, status_reason, owner_token, created_at_epoch, "
                    "updated_at_epoch, finished_at_epoch, result_json, "
                    "planner_version, proposal_ids_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (task_id, tj, workspace_root, status, status_reason,
                     owner_token, now, now,
                     now if status in TASK_TERMINAL_STATUSES else None,
                     None, planner_version, pid))
                conn.commit()
                return {"ok": True, "created": True, "idempotent": False,
                        "task_id": task_id, "status": status}
            except sqlite3.IntegrityError:
                # Concurrent identical insert lost the race: re-read the row.
                row = conn.execute(
                    "SELECT task_json, workspace_root, status FROM tasks "
                    "WHERE task_id = ?", (task_id,)).fetchone()
                if row is not None and row["task_json"] == tj and \
                        row["workspace_root"] == workspace_root:
                    return {"ok": True, "created": False, "idempotent": True,
                            "task_id": task_id, "status": row["status"]}
                return {"ok": False, "created": False,
                        "error": f"task_id {task_id!r} exists with "
                                 f"conflicting data"}
        finally:
            conn.close()

    def get_task(self, task_id):
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?",
                               (task_id,)).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def list_tasks(self, status=None):
        """Read-only system-level enumeration of task summaries.

        ``status=None`` returns every task; otherwise only tasks in that exact
        status. Deterministic (task_id ASC). Unknown status strings fail
        closed with an empty list (nothing is queried). Never mutates and
        never touches the production knowledge database — this is the public
        surface that replaces private ``_connect`` scans (Layer 6F).
        """
        if status is not None and status not in TASK_STATUSES:
            return []
        conn = self._connect()
        try:
            if status is None:
                rows = conn.execute(
                    "SELECT task_id, status, status_reason, owner_token, "
                    "created_at_epoch, updated_at_epoch, finished_at_epoch, "
                    "planner_version FROM tasks ORDER BY task_id ASC"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT task_id, status, status_reason, owner_token, "
                    "created_at_epoch, updated_at_epoch, finished_at_epoch, "
                    "planner_version FROM tasks WHERE status = ? "
                    "ORDER BY task_id ASC", (status,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update_task_status(self, task_id, status, expected_status=None,
                           status_reason=None, finished_at_epoch=None):
        """Guarded task status transition (compare-and-set semantics).

        The transition must be legal per ``TASK_TRANSITIONS``. When
        ``expected_status`` is supplied it must also match the current row
        (atomic WHERE guard). Any violation fails closed without modifying
        the row. Terminal transitions record ``finished_at_epoch`` (metadata).
        """
        if status not in TASK_STATUSES:
            return {"ok": False, "task_id": task_id, "to": status,
                    "updated": False,
                    "error": f"invalid task status {status!r}"}
        conn = self._connect()
        try:
            if expected_status is not None:
                if (expected_status, status) not in TASK_TRANSITIONS:
                    return {"ok": False, "task_id": task_id,
                            "from": expected_status, "to": status,
                            "updated": False,
                            "error": f"invalid transition "
                                     f"{expected_status}->{status}"}
                exists = conn.execute(
                    "SELECT 1 FROM tasks WHERE task_id = ?",
                    (task_id,)).fetchone()
                if exists is None:
                    return {"ok": False, "task_id": task_id,
                            "to": status, "updated": False,
                            "error": "task not found"}
                fin = finished_at_epoch
                if fin is None and status in TASK_TERMINAL_STATUSES:
                    fin = time.time()
                cur = conn.execute(
                    "UPDATE tasks SET status = ?, updated_at_epoch = ?, "
                    "status_reason = COALESCE(?, status_reason), "
                    "finished_at_epoch = COALESCE(?, finished_at_epoch) "
                    "WHERE task_id = ? AND status = ?",
                    (status, time.time(), status_reason, fin, task_id,
                     expected_status))
                conn.commit()
                return {"ok": True, "task_id": task_id,
                        "from": expected_status, "to": status,
                        "updated": cur.rowcount > 0}
            row = conn.execute("SELECT status FROM tasks WHERE task_id = ?",
                               (task_id,)).fetchone()
            if row is None:
                return {"ok": False, "task_id": task_id, "to": status,
                        "updated": False, "error": "task not found"}
            current = row["status"]
            if (current, status) not in TASK_TRANSITIONS:
                return {"ok": False, "task_id": task_id, "from": current,
                        "to": status, "updated": False,
                        "error": f"invalid transition {current}->{status}"}
            fin = finished_at_epoch
            if fin is None and status in TASK_TERMINAL_STATUSES:
                fin = time.time()
            cur = conn.execute(
                "UPDATE tasks SET status = ?, updated_at_epoch = ?, "
                "status_reason = COALESCE(?, status_reason), "
                "finished_at_epoch = COALESCE(?, finished_at_epoch) "
                "WHERE task_id = ? AND status = ?",
                (status, time.time(), status_reason, fin, task_id, current))
            conn.commit()
            return {"ok": True, "task_id": task_id, "from": current,
                    "to": status, "updated": cur.rowcount > 0}
        finally:
            conn.close()

    def update_task_result(self, task_id, result_json, planner_version=None):
        """Persist the final task result (JSON). Status is not changed here."""
        res, err = self._canonical_json(result_json, "result_json")
        if err:
            return {"ok": False, "task_id": task_id, "error": err}
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE tasks SET result_json = ?, "
                "planner_version = COALESCE(?, planner_version), "
                "updated_at_epoch = ? WHERE task_id = ?",
                (res, planner_version, time.time(), task_id))
            conn.commit()
            return {"ok": True, "task_id": task_id,
                    "updated": cur.rowcount > 0}
        finally:
            conn.close()

    def claim_task(self, task_id, owner_token):
        """Atomically claim a ``planned`` task for a single owner.

        A single ``UPDATE ... WHERE status = 'planned'`` statement is atomic
        at the database level, so exactly one concurrent caller flips the row
        to ``running``. No global process lock is used. Missing/invalid
        owner_token fails closed.
        """
        if not isinstance(owner_token, str) or not owner_token:
            return {"ok": False, "task_id": task_id, "claimed": False,
                    "status": None,
                    "error": "owner_token must be a non-empty string"}
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE tasks SET status = 'running', owner_token = ?, "
                "updated_at_epoch = ? "
                "WHERE task_id = ? AND status = 'planned'",
                (owner_token, time.time(), task_id))
            conn.commit()
            claimed = cur.rowcount == 1
            return {"ok": True, "task_id": task_id, "claimed": claimed,
                    "status": "running" if claimed else None,
                    "error": None if claimed else
                             "task not claimed (missing, already running, "
                             "or terminal)"}
        finally:
            conn.close()

    def ensure_task_step(self, task_id, step_id, idx, tool, inputs_json,
                         status="pending", result_json=None, error=None,
                         operation_id=None, started_at_epoch=None,
                         finished_at_epoch=None, duration=None):
        """Persist/ensure a task step (write-ahead intent record).

        Idempotent for identical data; conflicting data for the same
        (task_id, step_id) fails closed. Steps default to ``pending`` so 6B
        can advance them via ``update_task_step_status``. FK requires the
        owning task row to exist first.
        """
        if not isinstance(task_id, str) or not task_id:
            return {"ok": False, "error": "task_id must be a non-empty string"}
        if not isinstance(step_id, str) or not step_id:
            return {"ok": False, "error": "step_id must be a non-empty string"}
        if not isinstance(idx, int):
            return {"ok": False, "error": "idx must be an integer"}
        if not isinstance(tool, str) or not tool:
            return {"ok": False, "error": "tool must be a non-empty string"}
        if status not in TASK_STEP_STATUSES:
            return {"ok": False,
                    "error": f"invalid step status {status!r}"}
        ij, ij_err = self._canonical_json(inputs_json, "inputs_json")
        if ij_err:
            return {"ok": False, "error": ij_err}
        rj, rj_err = self._canonical_json(result_json, "result_json")
        if rj_err:
            return {"ok": False, "error": rj_err}
        conn = self._connect()
        try:
            prev = conn.execute(
                "SELECT idx, tool, inputs_json FROM task_steps "
                "WHERE task_id = ? AND step_id = ?",
                (task_id, step_id)).fetchone()
            if prev is not None:
                if prev["idx"] == idx and prev["tool"] == tool and \
                        prev["inputs_json"] == ij:
                    return {"ok": True, "created": False, "idempotent": True,
                            "task_id": task_id, "step_id": step_id}
                return {"ok": False, "created": False,
                        "error": f"step {step_id!r} for task {task_id!r} "
                                 f"exists with conflicting idx/tool/inputs"}
            try:
                conn.execute(
                    "INSERT INTO task_steps (task_id, step_id, idx, tool, "
                    "inputs_json, status, result_json, error, operation_id, "
                    "started_at_epoch, finished_at_epoch, duration) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (task_id, step_id, idx, tool, ij, status, rj, error,
                     operation_id, started_at_epoch, finished_at_epoch,
                     duration))
                conn.commit()
                return {"ok": True, "created": True, "idempotent": False,
                        "task_id": task_id, "step_id": step_id}
            except sqlite3.IntegrityError:
                # FK failure (no such task) or lost identical insert race.
                again = conn.execute(
                    "SELECT idx, tool, inputs_json FROM task_steps "
                    "WHERE task_id = ? AND step_id = ?",
                    (task_id, step_id)).fetchone()
                if again is not None and again["idx"] == idx and \
                        again["tool"] == tool and again["inputs_json"] == ij:
                    return {"ok": True, "created": False, "idempotent": True,
                            "task_id": task_id, "step_id": step_id}
                return {"ok": False, "created": False,
                        "error": f"task step not insertable for task "
                                 f"{task_id!r}: task may not exist or step "
                                 f"conflicts"}
        finally:
            conn.close()

    def get_task_step(self, task_id, step_id):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM task_steps WHERE task_id = ? AND step_id = ?",
                (task_id, step_id)).fetchone()
            return dict(row) if row is not None else None
        finally:
            conn.close()

    def list_task_steps(self, task_id):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM task_steps WHERE task_id = ? ORDER BY idx ASC",
                (task_id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def update_task_step_status(self, task_id, step_id, status,
                                expected_status=None):
        """Guarded task step status transition (compare-and-set semantics).

        Legal transitions per ``TASK_STEP_TRANSITIONS``
        (pending->running->success/failed/skipped). Terminal step statuses
        record ``finished_at_epoch`` (metadata). Fail closed otherwise.
        """
        if status not in TASK_STEP_STATUSES:
            return {"ok": False, "task_id": task_id, "step_id": step_id,
                    "to": status, "updated": False,
                    "error": f"invalid step status {status!r}"}
        conn = self._connect()
        try:
            if expected_status is not None:
                if (expected_status, status) not in TASK_STEP_TRANSITIONS:
                    return {"ok": False, "task_id": task_id,
                            "step_id": step_id, "from": expected_status,
                            "to": status, "updated": False,
                            "error": f"invalid step transition "
                                     f"{expected_status}->{status}"}
                cur = conn.execute(
                    "UPDATE task_steps SET status = ?, finished_at_epoch = "
                    "COALESCE(?, finished_at_epoch) "
                    "WHERE task_id = ? AND step_id = ? AND status = ?",
                    (status,
                     time.time() if status in ("success", "failed", "skipped")
                     else None,
                     task_id, step_id, expected_status))
                conn.commit()
                return {"ok": True, "task_id": task_id, "step_id": step_id,
                        "from": expected_status, "to": status,
                        "updated": cur.rowcount > 0}
            row = conn.execute(
                "SELECT status FROM task_steps "
                "WHERE task_id = ? AND step_id = ?",
                (task_id, step_id)).fetchone()
            if row is None:
                return {"ok": False, "task_id": task_id, "step_id": step_id,
                        "to": status, "updated": False,
                        "error": "step not found"}
            current = row["status"]
            if (current, status) not in TASK_STEP_TRANSITIONS:
                return {"ok": False, "task_id": task_id, "step_id": step_id,
                        "from": current, "to": status, "updated": False,
                        "error": f"invalid step transition "
                                 f"{current}->{status}"}
            cur = conn.execute(
                "UPDATE task_steps SET status = ?, finished_at_epoch = "
                "COALESCE(?, finished_at_epoch) "
                "WHERE task_id = ? AND step_id = ? AND status = ?",
                (status,
                 time.time() if status in ("success", "failed", "skipped")
                 else None,
                 task_id, step_id, current))
            conn.commit()
            return {"ok": True, "task_id": task_id, "step_id": step_id,
                    "from": current, "to": status,
                    "updated": cur.rowcount > 0}
        finally:
            conn.close()

    def update_step_result(self, task_id, step_id, result_json=None,
                           error=None, started_at_epoch=None,
                           finished_at_epoch=None, duration=None):
        """Update a step's result/error/timing metadata (not its status)."""
        rj, rj_err = None, None
        if result_json is not None:
            rj, rj_err = self._canonical_json(result_json, "result_json")
            if rj_err:
                return {"ok": False, "task_id": task_id, "step_id": step_id,
                        "error": rj_err}
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE task_steps SET "
                "result_json = COALESCE(?, result_json), "
                "error = COALESCE(?, error), "
                "started_at_epoch = COALESCE(?, started_at_epoch), "
                "finished_at_epoch = COALESCE(?, finished_at_epoch), "
                "duration = COALESCE(?, duration) "
                "WHERE task_id = ? AND step_id = ?",
                (rj, error, started_at_epoch, finished_at_epoch, duration,
                 task_id, step_id))
            conn.commit()
            return {"ok": True, "task_id": task_id, "step_id": step_id,
                    "updated": cur.rowcount > 0}
        finally:
            conn.close()

    def attach_operation(self, task_id, step_id, operation_id):
        """Link a task step to the write operation it produced (for 6D
        task-level rollback aggregation)."""
        if not isinstance(operation_id, str) or not operation_id:
            return {"ok": False, "task_id": task_id, "step_id": step_id,
                    "error": "operation_id must be a non-empty string"}
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE task_steps SET operation_id = ? "
                "WHERE task_id = ? AND step_id = ?",
                (operation_id, task_id, step_id))
            conn.commit()
            return {"ok": True, "task_id": task_id, "step_id": step_id,
                    "operation_id": operation_id,
                    "updated": cur.rowcount > 0}
        finally:
            conn.close()
