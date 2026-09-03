"""Knowledge lifecycle events persistence (Phase 5).

Lifecycle *events* are stored in ``database/evidence.db`` (the intelligence
audit database) in an append-only ``knowledge_lifecycle_events`` table. The
production ``knowledge.db`` schema is never modified; only the existing
``nodes.metadata`` JSON column is updated (via KnowledgeRepository), and then
only after approval gates pass.

Events are immutable: INSERT and SELECT only. UPDATE and DELETE are rejected at
the database level so the audit trail of lifecycle changes cannot be rewritten.
"""

import hashlib
import json
import os
import sqlite3

_DEFAULT_EVIDENCE_DB = None


def default_evidence_db_path():
    global _DEFAULT_EVIDENCE_DB
    if _DEFAULT_EVIDENCE_DB is None:
        _ROOT = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _DEFAULT_EVIDENCE_DB = os.path.join(_ROOT, "database", "evidence.db")
    return _DEFAULT_EVIDENCE_DB


_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_lifecycle_events (
    event_id        TEXT PRIMARY KEY,
    knowledge_id    TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    old_value_json  TEXT,
    new_value_json  TEXT,
    evidence_ids_json TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'applied',
    note            TEXT,
    created_at_epoch REAL NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_knowledge_lifecycle_no_update
BEFORE UPDATE ON knowledge_lifecycle_events
BEGIN
    SELECT RAISE(ABORT, 'knowledge_lifecycle_events is append-only (UPDATE rejected)');
END;

CREATE TRIGGER IF NOT EXISTS trg_knowledge_lifecycle_no_delete
BEFORE DELETE ON knowledge_lifecycle_events
BEGIN
    SELECT RAISE(ABORT, 'knowledge_lifecycle_events is append-only (DELETE rejected)');
END;
"""


class KnowledgeLifecycleEvent:
    """An immutable, auditable lifecycle event for a knowledge node."""

    __slots__ = (
        "event_id", "knowledge_id", "event_type", "old_value", "new_value",
        "evidence_ids", "status", "note", "created_at_epoch",
    )

    def __init__(self, event_id, knowledge_id, event_type, old_value,
                 new_value, evidence_ids=(), status="applied", note=None,
                 created_at_epoch=0.0):
        self.event_id = event_id
        self.knowledge_id = knowledge_id
        self.event_type = event_type
        self.old_value = old_value
        self.new_value = new_value
        self.evidence_ids = tuple(evidence_ids)
        self.status = status
        self.note = note
        self.created_at_epoch = float(created_at_epoch)

    def to_row(self):
        return (
            self.event_id, self.knowledge_id, self.event_type,
            json.dumps(self.old_value, sort_keys=True, default=str),
            json.dumps(self.new_value, sort_keys=True, default=str),
            json.dumps(list(self.evidence_ids), separators=(",", ":")),
            self.status, self.note, self.created_at_epoch,
        )

    @classmethod
    def from_row(cls, row):
        return cls(
            event_id=row["event_id"],
            knowledge_id=row["knowledge_id"],
            event_type=row["event_type"],
            old_value=json.loads(row["old_value_json"]),
            new_value=json.loads(row["new_value_json"]),
            evidence_ids=json.loads(row["evidence_ids_json"] or "[]"),
            status=row["status"],
            note=row["note"],
            created_at_epoch=row["created_at_epoch"],
        )


def derive_event_id(knowledge_id, event_type, old_value, new_value,
                    evidence_ids, created_at_epoch):
    """Deterministic event id (idempotent). Prefix ``kev_`` + 32 hex."""
    token = json.dumps(
        {"knowledge_id": knowledge_id, "event_type": event_type,
         "old": json.dumps(old_value, sort_keys=True, default=str),
         "new": json.dumps(new_value, sort_keys=True, default=str),
         "evidence": sorted(evidence_ids or ()),
         "created_at_epoch": created_at_epoch},
        sort_keys=True, separators=(",", ":"))
    return "kev_" + hashlib.sha256(token.encode("utf-8")).hexdigest()


class LifecycleEventStore:
    """Append-only persistence for lifecycle events."""

    def __init__(self, db_path=None, check_same_thread=True):
        self.db_path = db_path or default_evidence_db_path()
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

    def save(self, event):
        with self.conn:
            self.conn.execute(
                "INSERT OR ignore INTO knowledge_lifecycle_events "
                "(event_id, knowledge_id, event_type, old_value_json, "
                " new_value_json, evidence_ids_json, status, note, "
                " created_at_epoch) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                event.to_row(),
            )
        return event.event_id

    def get(self, event_id):
        row = self.conn.execute(
            "SELECT * FROM knowledge_lifecycle_events WHERE event_id=?",
            (event_id,)).fetchone()
        if row is None:
            return None
        return KnowledgeLifecycleEvent.from_row(row)

    def for_knowledge(self, knowledge_id):
        rows = self.conn.execute(
            "SELECT * FROM knowledge_lifecycle_events WHERE knowledge_id=? "
            "ORDER BY created_at_epoch",
            (knowledge_id,)).fetchall()
        return [KnowledgeLifecycleEvent.from_row(r) for r in rows]

    def all(self):
        rows = self.conn.execute(
            "SELECT * FROM knowledge_lifecycle_events ORDER BY created_at_epoch"
        ).fetchall()
        return [KnowledgeLifecycleEvent.from_row(r) for r in rows]
