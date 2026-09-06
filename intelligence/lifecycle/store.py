"""Lifecycle record persistence — lifecycle_records + append-only audit (Phase 26).

The lifecycle layer owns the canonical provenance / role / state metadata for
every intelligence record. It lives in ``database/evidence.db`` (the same
audit DB as evidence/outcomes/strategies) in two tables:

    lifecycle_records — one row per lifecycle-recorded entity.
        record_id            TEXT PRIMARY KEY
        role                 TEXT
        origin               TEXT
        lifecycle_state      TEXT
        subject              TEXT
        source               TEXT
        actor                TEXT
        timestamp_epoch      REAL
        project              TEXT
        context_id           TEXT
        evidence_ids_json    TEXT
        confidence           REAL
        parent_record_ids_json TEXT
        derived_from_json    TEXT
        content_json         TEXT
        created_at_epoch     REAL
        updated_at_epoch     REAL

    lifecycle_audit — append-only; one immutable row per state transition.
        audit_id             TEXT PRIMARY KEY
        record_id            TEXT
        previous_state       TEXT
        new_state            TEXT
        note                 TEXT
        created_at_epoch     REAL
        (UPDATE/DELETE rejected at the DB level by triggers)

Conventions mirror the existing stores: database is storage, not
user-taught data; state transitions are validated against the canonical state
machine in ``intelligence.lifecycle.model`` before being applied.
"""

import json
import os
import sqlite3
import time

from intelligence.lifecycle.model import (
    LifecycleRecord,
    LifecycleState,
    Origin,
    Provenance,
    RecordRole,
    canonical_state_transition,
    coerce_origin,
    coerce_role,
    coerce_state,
)

from ai_engine.paths import get_legacy_db_path

_DEFAULT_EVIDENCE_DB = None


def default_evidence_db_path():
    global _DEFAULT_EVIDENCE_DB
    if _DEFAULT_EVIDENCE_DB is None:
        _DEFAULT_EVIDENCE_DB = get_legacy_db_path("evidence.db")
    return _DEFAULT_EVIDENCE_DB


_SCHEMA = """
CREATE TABLE IF NOT EXISTS lifecycle_records (
    record_id              TEXT PRIMARY KEY,
    role                   TEXT NOT NULL,
    origin                 TEXT NOT NULL,
    lifecycle_state        TEXT NOT NULL,
    subject                TEXT,
    source                 TEXT,
    actor                  TEXT,
    timestamp_epoch        REAL,
    project                TEXT,
    context_id             TEXT,
    evidence_ids_json      TEXT,
    confidence             REAL,
    parent_record_ids_json TEXT,
    derived_from_json      TEXT,
    content_json           TEXT,
    created_at_epoch       REAL,
    updated_at_epoch       REAL
);

CREATE TABLE IF NOT EXISTS lifecycle_audit (
    audit_id         TEXT PRIMARY KEY,
    record_id        TEXT NOT NULL,
    previous_state   TEXT NOT NULL,
    new_state        TEXT NOT NULL,
    note             TEXT,
    created_at_epoch REAL NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_lifecycle_audit_no_update
BEFORE UPDATE ON lifecycle_audit
BEGIN
    SELECT RAISE(ABORT, 'lifecycle_audit is append-only (UPDATE rejected)');
END;

CREATE TRIGGER IF NOT EXISTS trg_lifecycle_audit_no_delete
BEFORE DELETE ON lifecycle_audit
BEGIN
    SELECT RAISE(ABORT, 'lifecycle_audit is append-only (DELETE rejected)');
END;
"""


class LifecycleRecordStore:
    """SQLite-backed lifecycle metadata store (evidence.db).

    Only this store may know the table layout; model and trace modules stay
    table-name-free so higher layers never depend on SQL schema.
    """

    def __init__(self, db_path=None, check_same_thread=True):
        self.db_path = db_path or default_evidence_db_path()
        parent = os.path.dirname(self.db_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
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

    # -- serialization helpers ---------------------------------------------

    @staticmethod
    def _canonical_json(obj):
        return json.dumps(obj, sort_keys=True, default=str)

    def _row_to_record(self, row):
        prov = Provenance(
            record_id=row["record_id"],
            origin=coerce_origin(row["origin"]) or Origin.USER_PROVIDED,
            source=row["source"],
            actor=row["actor"],
            timestamp=row["timestamp_epoch"],
            project=row["project"],
            context_id=row["context_id"],
            evidence_ids=tuple(json.loads(row["evidence_ids_json"] or "[]")),
            confidence=row["confidence"],
            lifecycle_state=coerce_state(row["lifecycle_state"])
            or LifecycleState.ACTIVE,
            parent_record_ids=tuple(
                json.loads(row["parent_record_ids_json"] or "[]")),
            derived_from=tuple(json.loads(row["derived_from_json"] or "[]")),
            created_at=row["created_at_epoch"],
            updated_at=row["updated_at_epoch"],
        )
        return LifecycleRecord(
            record_id=row["record_id"],
            role=coerce_role(row["role"]) or RecordRole.MEMORY,
            provenance=prov,
            subject=row["subject"],
            content=json.loads(row["content_json"] or "{}"),
        )

    # -- writes ------------------------------------------------------------

    def save(self, record):
        """Insert a lifecycle record (idempotent via INSERT OR IGNORE)."""
        prov = record.provenance
        with self.conn:
            self.conn.execute(
                "INSERT OR ignore INTO lifecycle_records "
                "(record_id, role, origin, lifecycle_state, subject, "
                " source, actor, timestamp_epoch, project, context_id, "
                " evidence_ids_json, confidence, parent_record_ids_json, "
                " derived_from_json, content_json, created_at_epoch, "
                " updated_at_epoch) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    record.record_id,
                    record.role.value,
                    prov.origin.value,
                    prov.lifecycle_state.value,
                    record.subject,
                    prov.source,
                    prov.actor,
                    prov.timestamp,
                    prov.project,
                    prov.context_id,
                    self._canonical_json(list(prov.evidence_ids)),
                    prov.confidence,
                    self._canonical_json(list(prov.parent_record_ids)),
                    self._canonical_json(list(prov.derived_from)),
                    self._canonical_json(record.content),
                    prov.created_at,
                    prov.updated_at,
                ),
            )
        return record.record_id

    def set_state(self, record_id, new_state, note=None,
                  created_at_epoch=None):
        """Validated state transition; audited. Returns updated record.

        Raises KeyError if the record does not exist; raises ValueError if the
        transition is rejected by the canonical state machine.
        """
        row = self.conn.execute(
            "SELECT * FROM lifecycle_records WHERE record_id=?",
            (record_id,)).fetchone()
        if row is None:
            raise KeyError(record_id)
        current = coerce_state(row["lifecycle_state"]) or LifecycleState.ACTIVE
        new = coerce_state(new_state)
        if new is None:
            raise ValueError("invalid target lifecycle state %r" % (new_state,))
        if current is new:
            raise ValueError("state is already %r" % new.value)
        ok, reason = canonical_state_transition(current, new)
        if not ok:
            raise ValueError(reason)
        created_at_epoch = created_at_epoch or time.time()
        with self.conn:
            self.conn.execute(
                "UPDATE lifecycle_records SET lifecycle_state=?, "
                "updated_at_epoch=? WHERE record_id=?",
                (new.value, created_at_epoch, record_id),
            )
            audit_id = "la_" + __import__("hashlib").sha256(
                json.dumps({
                    "record_id": record_id,
                    "previous_state": current.value,
                    "new_state": new.value,
                    "note": note,
                    "created_at_epoch": created_at_epoch,
                }, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()[:32]
            self.conn.execute(
                "INSERT OR ignore INTO lifecycle_audit "
                "(audit_id, record_id, previous_state, new_state, note, "
                " created_at_epoch) VALUES (?,?,?,?,?,?)",
                (audit_id, record_id, current.value, new.value, note,
                 created_at_epoch),
            )
        return self.get(record_id)

    # -- reads -------------------------------------------------------------

    def get(self, record_id):
        row = self.conn.execute(
            "SELECT * FROM lifecycle_records WHERE record_id=?",
            (record_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def get_any_role(self, record_id):
        """Look up a record by its lifecycle id or any native record id."""
        return self.get(record_id)

    def for_role(self, role):
        role = coerce_role(role)
        if role is None:
            return []
        rows = self.conn.execute(
            "SELECT * FROM lifecycle_records WHERE role=? "
            "ORDER BY record_id", (role.value,)).fetchall()
        return [self._row_to_record(r) for r in rows]

    def for_project(self, project, role=None):
        if role is not None:
            role = coerce_role(role)
            if role is None:
                return []
            rows = self.conn.execute(
                "SELECT * FROM lifecycle_records WHERE project=? AND role=? "
                "ORDER BY record_id", (project, role.value)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM lifecycle_records WHERE project=? "
                "ORDER BY record_id", (project,)).fetchall()
        return [self._row_to_record(r) for r in rows]

    def all(self):
        rows = self.conn.execute(
            "SELECT * FROM lifecycle_records ORDER BY record_id").fetchall()
        return [self._row_to_record(r) for r in rows]

    def count(self):
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM lifecycle_records").fetchone()
        return row["n"]

    def count_by_role(self):
        rows = self.conn.execute(
            "SELECT role AS k, COUNT(*) AS n FROM lifecycle_records "
            "GROUP BY role").fetchall()
        return {r["k"]: r["n"] for r in rows}

    def count_by_origin(self):
        rows = self.conn.execute(
            "SELECT origin AS k, COUNT(*) AS n FROM lifecycle_records "
            "GROUP BY origin").fetchall()
        return {r["k"]: r["n"] for r in rows}

    def count_by_state(self):
        rows = self.conn.execute(
            "SELECT lifecycle_state AS k, COUNT(*) AS n "
            "FROM lifecycle_records GROUP BY lifecycle_state").fetchall()
        return {r["k"]: r["n"] for r in rows}

    def audit_trail(self, record_id=None):
        if record_id is not None:
            rows = self.conn.execute(
                "SELECT * FROM lifecycle_audit WHERE record_id=? "
                "ORDER BY created_at_epoch, audit_id", (record_id,)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM lifecycle_audit "
                "ORDER BY created_at_epoch, audit_id").fetchall()
        return [dict(r) for r in rows]