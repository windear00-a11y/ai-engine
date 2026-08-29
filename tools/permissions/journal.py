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
    created_at_epoch REAL
);
CREATE INDEX IF NOT EXISTS idx_journal_operation
    ON journal(operation_id);

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
    """Thin transactional wrapper around the engine state DB."""

    def __init__(self, db_path=None):
        self.db_path = db_path or _default_state_db()
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
            conn.commit()
        finally:
            conn.close()

    # -- journal -----------------------------------------------------------

    def add_journal(self, operation_id, target_rel, tier, content,
                    status="snapshot_stored"):
        """Persist a before-image snapshot for an approved write.

        Returns the journal record dict, or a structured error dict when
        content cannot be snapshotted (fail-closed; caller must not write).
        """
        try:
            data = content if isinstance(content, bytes) else content.encode(
                "utf-8")
        except (AttributeError, UnicodeEncodeError):
            return {"ok": False, "error": "snapshot content not serializable"}
        size = len(data)
        digest = checksum_bytes(data)
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO journal (operation_id, target_rel, tier, "
                "checksum, size_bytes, content, status, created_at_epoch) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (operation_id, target_rel, tier, digest, size,
                 sqlite3.Binary(data), status, time.time()))
            conn.commit()
            seq = cur.lastrowid
            return {"ok": True, "seq": seq, "operation_id": operation_id,
                    "target_rel": target_rel, "tier": tier,
                    "checksum": digest, "size_bytes": size, "status": status}
        finally:
            conn.close()

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
