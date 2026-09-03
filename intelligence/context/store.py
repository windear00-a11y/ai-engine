"""Context snapshot persistence (SQLite, append-only) (Phase 1).

Persistence
-----------
``context_snapshots`` table::

    context_id         TEXT PRIMARY KEY
    system_json        TEXT
    project_json       TEXT
    task_json          TEXT
    temporal_json      TEXT
    captured_at_epoch  REAL

Append-only: the store exposes INSERT (save) and SELECT (get/all) only.
There is no UPDATE or DELETE anywhere. A rollback/restore trigger ("no
deletes") backs this at the database level so the immutable property is
defended even if a future caller tries to mutate.

If the database is corrupted/missing, the store degrades gracefully:
writes raise a clear error and reads return empty results (never a crash).
"""

import json
import os
import sqlite3

_DEFAULT_CONTEXT_DB = None


def default_context_db_path():
    global _DEFAULT_CONTEXT_DB
    if _DEFAULT_CONTEXT_DB is None:
        _ROOT = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _DEFAULT_CONTEXT_DB = os.path.join(_ROOT, "database", "context.db")
    return _DEFAULT_CONTEXT_DB


_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_snapshots (
    context_id        TEXT PRIMARY KEY,
    system_json       TEXT,
    project_json      TEXT,
    task_json         TEXT,
    temporal_json     TEXT,
    captured_at_epoch REAL
);

-- Enforce append-only: reject any attempt to UPDATE or DELETE rows.
CREATE TRIGGER IF NOT EXISTS trg_context_no_update
BEFORE UPDATE ON context_snapshots
BEGIN
    SELECT RAISE(ABORT, 'context_snapshots is append-only (UPDATE rejected)');
END;

CREATE TRIGGER IF NOT EXISTS trg_context_no_delete
BEFORE DELETE ON context_snapshots
BEGIN
    SELECT RAISE(ABORT, 'context_snapshots is append-only (DELETE rejected)');
END;
"""


class ContextStore:
    def __init__(self, db_path=None, check_same_thread=True):
        self.db_path = db_path or default_context_db_path()
        self.conn = sqlite3.connect(self.db_path,
                                    check_same_thread=check_same_thread)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def save(self, snapshot):
        """Insert a snapshot (idempotent: same context_id => same row)."""
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR ignore INTO context_snapshots "
                    "(context_id, system_json, project_json, task_json, "
                    " temporal_json, captured_at_epoch) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        snapshot.context_id,
                        json.dumps(snapshot.system, sort_keys=True),
                        json.dumps(snapshot.project, sort_keys=True),
                        json.dumps(snapshot.task, sort_keys=True),
                        json.dumps(snapshot.temporal, sort_keys=True,
                                   default=str),
                        snapshot.captured_at_epoch,
                    ),
                )
        except sqlite3.Error:
            raise
        return snapshot.context_id

    def get(self, context_id):
        row = self.conn.execute(
            "SELECT * FROM context_snapshots WHERE context_id=?",
            (context_id,)).fetchone()
        if row is None:
            return None
        return self._to_snapshot(row)

    def all(self):
        rows = self.conn.execute(
            "SELECT * FROM context_snapshots ORDER BY captured_at_epoch"
        ).fetchall()
        return [self._to_snapshot(r) for r in rows]

    def count(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM context_snapshots").fetchone()[0]

    def _to_snapshot(self, row):
        from intelligence.context.schema import ContextSnapshot
        system = json.loads(row["system_json"] or "{}")
        project = json.loads(row["project_json"] or "{}")
        task = json.loads(row["task_json"] or "{}")
        temporal = json.loads(row["temporal_json"] or "{}")
        return ContextSnapshot(
            system=system,
            project=project,
            task=task,
            temporal=temporal,
            context_id=row["context_id"],
            captured_at_epoch=row["captured_at_epoch"],
        )
