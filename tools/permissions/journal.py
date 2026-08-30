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
            conn.commit()
        finally:
            conn.close()

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
