"""Reasoning output persistence (Phase 6).

Reasoning conclusions are stored in ``database/evidence.db`` in an append-only
``reasoning_outputs`` table. Reasoning is read-only/advisory, but its outputs
are auditable so later phases (Decision, Learning) can consume and trace them.
"""

import json
import sqlite3

from ai_engine.paths import get_legacy_db_path

_DEFAULT_EVIDENCE_DB = None


def default_evidence_db_path():
    global _DEFAULT_EVIDENCE_DB
    if _DEFAULT_EVIDENCE_DB is None:
        _DEFAULT_EVIDENCE_DB = get_legacy_db_path("evidence.db")
    return _DEFAULT_EVIDENCE_DB


_SCHEMA = """
CREATE TABLE IF NOT EXISTS reasoning_outputs (
    reasoning_id             TEXT PRIMARY KEY,
    query_json               TEXT NOT NULL,
    context_id               TEXT NOT NULL,
    conclusions_json         TEXT NOT NULL,
    contradictions_json      TEXT NOT NULL,
    insufficient_evidence_json TEXT NOT NULL,
    created_at_epoch         REAL NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_reasoning_outputs_no_update
BEFORE UPDATE ON reasoning_outputs
BEGIN
    SELECT RAISE(ABORT, 'reasoning_outputs is append-only (UPDATE rejected)');
END;

CREATE TRIGGER IF NOT EXISTS trg_reasoning_outputs_no_delete
BEFORE DELETE ON reasoning_outputs
BEGIN
    SELECT RAISE(ABORT, 'reasoning_outputs is append-only (DELETE rejected)');
END;
"""


class ReasoningStore:
    """Append-only persistence for reasoning outputs."""

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

    def save(self, output):
        with self.conn:
            self.conn.execute(
                "INSERT OR ignore INTO reasoning_outputs "
                "(reasoning_id, query_json, context_id, conclusions_json, "
                " contradictions_json, insufficient_evidence_json, "
                " created_at_epoch) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    output.reasoning_id,
                    json.dumps(output.query, sort_keys=True, default=str),
                    output.context_id,
                    json.dumps([c.to_dict() for c in output.conclusions],
                               sort_keys=True, default=str),
                    json.dumps([c.to_dict() for c in output.contradictions],
                               sort_keys=True, default=str),
                    json.dumps([i.to_dict()
                                for i in output.insufficient_evidence],
                               sort_keys=True, default=str),
                    output.created_at_epoch,
                ),
            )
        return output.reasoning_id

    def get(self, reasoning_id):
        row = self.conn.execute(
            "SELECT * FROM reasoning_outputs WHERE reasoning_id=?",
            (reasoning_id,)).fetchone()
        if row is None:
            return None
        return dict(row)

    def all(self):
        rows = self.conn.execute(
            "SELECT * FROM reasoning_outputs ORDER BY created_at_epoch"
        ).fetchall()
        return [dict(r) for r in rows]
