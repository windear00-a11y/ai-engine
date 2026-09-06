"""Context snapshot persistence (SQLite, append-only) — generic Phase 4.

Persistence
-----------
``context_snapshots`` table (generic 8 dimensions):

    context_id         TEXT PRIMARY KEY
    environment_json   TEXT
    project_json       TEXT
    source_json        TEXT
    actor_json         TEXT
    spatial_json       TEXT
    social_json        TEXT
    affective_json     TEXT
    temporal_json      TEXT
    captured_at_epoch  REAL
    -- legacy columns for backward compat (kept for old readers only)
    system_json        TEXT
    task_json          TEXT

New code writes ONLY the generic columns; legacy ``system_json``/``task_json``
are no longer written on new snapshots (kept for reading older rows). Old DBs
(with 4 columns) are migrated via ALTER TABLE ADD COLUMN.
Append-only: triggers reject UPDATE/DELETE.
"""

import json
import os
import sqlite3

from ai_engine.paths import get_legacy_db_path

_DEFAULT_CONTEXT_DB = None


def default_context_db_path():
    global _DEFAULT_CONTEXT_DB
    if _DEFAULT_CONTEXT_DB is None:
        _DEFAULT_CONTEXT_DB = get_legacy_db_path("context.db")
    return _DEFAULT_CONTEXT_DB


# Generic schema (new) + legacy columns for compat
_SCHEMA = """
CREATE TABLE IF NOT EXISTS context_snapshots (
    context_id         TEXT PRIMARY KEY,
    environment_json   TEXT,
    project_json       TEXT,
    source_json        TEXT,
    actor_json         TEXT,
    spatial_json       TEXT,
    social_json        TEXT,
    affective_json     TEXT,
    temporal_json      TEXT,
    captured_at_epoch  REAL,
    system_json        TEXT,
    task_json          TEXT
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

# Legacy schema columns that may be missing in old DBs
_NEW_COLUMNS = {
    "environment_json": "TEXT",
    "source_json": "TEXT",
    "actor_json": "TEXT",
    "spatial_json": "TEXT",
    "social_json": "TEXT",
    "affective_json": "TEXT",
    # legacy columns that may be missing if DB was created with new schema first
    "system_json": "TEXT",
    "task_json": "TEXT",
    "project_json": "TEXT",
    "temporal_json": "TEXT",
}


def _migrate_schema(conn):
    """Idempotent migration: add missing columns if old DB exists."""
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(context_snapshots)")}
    for col, ddl in _NEW_COLUMNS.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE context_snapshots ADD COLUMN {col} {ddl}")


class ContextStore:
    def __init__(self, db_path=None, check_same_thread=True):
        self.db_path = db_path or default_context_db_path()
        # Ensure parent dir exists
        parent = os.path.dirname(self.db_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path,
                                    check_same_thread=check_same_thread)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        _migrate_schema(self.conn)
        self.conn.commit()

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def save(self, snapshot):
        """Insert a snapshot (idempotent: same context_id => same row)."""
        # Prepare JSON for all dimensions
        env_json = json.dumps(snapshot.environment, sort_keys=True, ensure_ascii=False)
        proj_json = json.dumps(snapshot.project, sort_keys=True, ensure_ascii=False)
        src_json = json.dumps(snapshot.source, sort_keys=True, ensure_ascii=False)
        actor_json = json.dumps(snapshot.actor, sort_keys=True, ensure_ascii=False)
        spatial_json = json.dumps(snapshot.spatial, sort_keys=True, ensure_ascii=False)
        social_json = json.dumps(snapshot.social, sort_keys=True, ensure_ascii=False)
        affective_json = json.dumps(snapshot.affective, sort_keys=True, ensure_ascii=False)
        temporal_json = json.dumps(snapshot.temporal, sort_keys=True, ensure_ascii=False, default=str)
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR ignore INTO context_snapshots "
                    "(context_id, environment_json, project_json, source_json, actor_json, "
                    " spatial_json, social_json, affective_json, temporal_json, "
                    " captured_at_epoch) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        snapshot.context_id,
                        env_json, proj_json, src_json, actor_json,
                        spatial_json, social_json, affective_json, temporal_json,
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
        # Prefer new columns if present and non-null, else fallback to legacy
        def _load(col_new, col_legacy=None):
            val = None
            try:
                val = row[col_new]
            except Exception:
                pass
            if val is None and col_legacy is not None:
                try:
                    val = row[col_legacy]
                except Exception:
                    val = None
            if val is None:
                return {}
            try:
                return json.loads(val) if val else {}
            except Exception:
                return {}

        env = _load("environment_json", "system_json")
        proj = _load("project_json", None)
        src = _load("source_json", "task_json")
        actor = _load("actor_json", None)
        spatial = _load("spatial_json", None)
        social = _load("social_json", None)
        affective = _load("affective_json", None)
        temporal = _load("temporal_json", None)

        # Fallback for old DBs where new columns are empty but legacy has data
        # Already handled via _load fallback

        return ContextSnapshot(
            environment=env,
            project=proj,
            source=src,
            actor=actor,
            spatial=spatial,
            social=social,
            affective=affective,
            temporal=temporal,
            context_id=row["context_id"],
            captured_at_epoch=row["captured_at_epoch"],
        )
