"""Generic first-class Activity (Phase 1).

Activity is the raw unit of happening — what was captured, before
interpretation into Knowledge/Evidence/Experience.

- Deterministic, content-addressed id (no UUID, no time in id).
- Append-only, per-project, stored in <data_root>/<project>/activity.db
- Strictly separate from engine_state.db (execution state). EngineState
  holds transient tasks (planned/running). Activity holds persistent diary
  history. This file never imports tools/permissions.

No network, no AI, stdlib-only.
"""

import hashlib
import json
import os
import sqlite3
import time

from ai_engine.paths import get_project_dir

_ACTIVITY_DB_NAME = "activity.db"


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def derive_activity_id(project_id, activity_type, source, payload, context_id=None):
    """Deterministic activity_id.

    Same (project_id, activity_type, source, payload) yields same id
    even if created_at differs (idempotent manual capture).
    Payload is canonicalized via sha256(canonical_json(payload)).
    """
    if payload is None:
        payload_hash = ""
    elif isinstance(payload, str):
        payload_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    else:
        payload_hash = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()

    # payload_hash is sufficient, but include small canonical slice for debug stability
    payload = {
        "project_id": project_id,
        "activity_type": activity_type,
        "source": source,
        "payload_hash": payload_hash,
        "context_id": context_id or "",
    }
    return "act_" + hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()[:32]


def get_activity_db_path(project_id, data_root=None):
    return os.path.join(get_project_dir(project_id, data_root), _ACTIVITY_DB_NAME)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS activities (
    activity_id      TEXT PRIMARY KEY,
    project_id       TEXT NOT NULL,
    activity_type    TEXT NOT NULL,
    source           TEXT NOT NULL,
    payload_json     TEXT NOT NULL,
    context_id       TEXT,
    created_at_epoch REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activities_project ON activities(project_id);
CREATE INDEX IF NOT EXISTS idx_activities_context ON activities(context_id);
"""


class ActivityStore:
    """Per-project Activity store (append-only).

    Never touches engine_state.db. Each project has its own activity.db.
    """

    def __init__(self, project_id, data_root=None):
        self.project_id = project_id
        self.data_root = data_root
        self.db_path = get_activity_db_path(project_id, data_root)
        self._init_schema()

    def _connect(self):
        # Ensure directory exists
        parent = os.path.dirname(self.db_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=5000")
        return con

    def _init_schema(self):
        con = self._connect()
        try:
            con.executescript(_SCHEMA)
            con.commit()
        finally:
            con.close()

    def save(self, activity_id, activity_type, source, payload, context_id=None, created_at_epoch=None):
        """Append an activity. Idempotent for identical (activity_id, payload).

        Returns {ok: True, created: bool, idempotent: bool} or {ok: False, error}
        Fail closed on duplicate activity_id with different payload.
        """
        if not isinstance(activity_id, str) or not activity_id:
            return {"ok": False, "error": "activity_id must be non-empty string"}
        if not isinstance(activity_type, str) or not activity_type.strip():
            return {"ok": False, "error": "activity_type must be non-empty string"}
        if not isinstance(source, str) or not source.strip():
            return {"ok": False, "error": "source must be non-empty string"}
        try:
            payload_json = _canonical(payload if payload is not None else {})
        except Exception as e:
            return {"ok": False, "error": f"payload not canonicalizable: {e}"}
        if created_at_epoch is None:
            created_at_epoch = time.time()

        con = self._connect()
        try:
            prev = con.execute("SELECT payload_json FROM activities WHERE activity_id = ?", (activity_id,)).fetchone()
            if prev is not None:
                if prev["payload_json"] == payload_json:
                    return {"ok": True, "created": False, "idempotent": True, "activity_id": activity_id}
                return {"ok": False, "created": False, "error": f"activity_id {activity_id!r} exists with different payload"}

            con.execute(
                "INSERT INTO activities (activity_id, project_id, activity_type, source, payload_json, context_id, created_at_epoch) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (activity_id, self.project_id, activity_type.strip(), source.strip(), payload_json, context_id, float(created_at_epoch)),
            )
            con.commit()
            return {"ok": True, "created": True, "idempotent": False, "activity_id": activity_id}
        except sqlite3.IntegrityError as e:
            # Race: check again
            prev = con.execute("SELECT payload_json FROM activities WHERE activity_id = ?", (activity_id,)).fetchone()
            if prev is not None and prev["payload_json"] == payload_json:
                return {"ok": True, "created": False, "idempotent": True, "activity_id": activity_id}
            return {"ok": False, "created": False, "error": str(e)}
        finally:
            con.close()

    def get(self, activity_id):
        con = self._connect()
        try:
            row = con.execute("SELECT * FROM activities WHERE activity_id = ?", (activity_id,)).fetchone()
            if row is None:
                return None
            out = dict(row)
            # Parse payload
            try:
                out["payload"] = json.loads(out["payload_json"])
            except Exception:
                out["payload"] = None
            return out
        finally:
            con.close()

    def list_activities(self, limit=100):
        con = self._connect()
        try:
            rows = con.execute("SELECT * FROM activities ORDER BY created_at_epoch ASC LIMIT ?", (limit,)).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                try:
                    d["payload"] = json.loads(d["payload_json"])
                except Exception:
                    d["payload"] = None
                out.append(d)
            return out
        finally:
            con.close()

    def count(self):
        con = self._connect()
        try:
            return con.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
        finally:
            con.close()

    def integrity_check(self):
        con = self._connect()
        try:
            row = con.execute("PRAGMA integrity_check").fetchone()
            return {"ok": row[0] == "ok", "result": row[0] if row else "no result"}
        finally:
            con.close()
