"""Evidence persistence (SQLite, append-only) (Phase 2).

``database/evidence.db`` stores the intelligence audit trail. Phase 2 adds the
``evidence`` table::

    evidence_id            TEXT PRIMARY KEY
    source_observation_id TEXT
    claim                 TEXT
    context_id            TEXT
    evidence_type         TEXT
    supporting_data_json  TEXT
    created_at_epoch      REAL

Append-only: only INSERT and SELECT are exposed. Triggers reject UPDATE and
DELETE so the immutability of evidence is defended at the database level.
"""

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
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id            TEXT PRIMARY KEY,
    source_observation_id TEXT,
    claim                 TEXT,
    context_id            TEXT,
    evidence_type         TEXT,
    supporting_data_json  TEXT,
    created_at_epoch      REAL
);

CREATE TRIGGER IF NOT EXISTS trg_evidence_no_update
BEFORE UPDATE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence is append-only (UPDATE rejected)');
END;

CREATE TRIGGER IF NOT EXISTS trg_evidence_no_delete
BEFORE DELETE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence is append-only (DELETE rejected)');
END;
"""


class EvidenceStore:
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

    def save(self, record):
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR ignore INTO evidence "
                    "(evidence_id, source_observation_id, claim, context_id, "
                    " evidence_type, supporting_data_json, created_at_epoch) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (
                        record.evidence_id,
                        record.source_observation_id,
                        record.claim,
                        record.context_id,
                        record.evidence_type.value,
                        json.dumps(record.supporting_data or {},
                                   sort_keys=True, default=str),
                        record.created_at_epoch,
                    ),
                )
        except sqlite3.Error:
            raise
        return record.evidence_id

    def get(self, evidence_id):
        row = self.conn.execute(
            "SELECT * FROM evidence WHERE evidence_id=?",
            (evidence_id,)).fetchone()
        return self._to_record(row) if row else None

    def for_claim(self, claim, context_id=None):
        if context_id is not None:
            rows = self.conn.execute(
                "SELECT * FROM evidence WHERE claim=? AND context_id=?",
                (claim, context_id)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM evidence WHERE claim=?", (claim,)).fetchall()
        return [self._to_record(r) for r in rows]

    def all(self):
        rows = self.conn.execute("SELECT * FROM evidence").fetchall()
        return [self._to_record(r) for r in rows]

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]

    def _to_record(self, row):
        from intelligence.evidence.schema import EvidenceRecord
        from intelligence.evidence.types import EvidenceType
        return EvidenceRecord(
            evidence_id=row["evidence_id"],
            source_observation_id=row["source_observation_id"],
            claim=row["claim"],
            context_id=row["context_id"],
            evidence_type=EvidenceType(row["evidence_type"]),
            supporting_data=json.loads(row["supporting_data_json"] or "{}"),
            created_at_epoch=row["created_at_epoch"],
        )
