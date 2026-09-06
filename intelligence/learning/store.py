"""Learning event persistence (Phase 8).

Learning events are immutable, append-only records in database/evidence.db
in the learning_events table.
"""

import json
import sqlite3

from ai_engine.paths import get_legacy_db_path


def default_evidence_db_path():
    return get_legacy_db_path("evidence.db")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS learning_events (
    learning_event_id TEXT PRIMARY KEY,
    outcome_id        TEXT NOT NULL,
    context_id        TEXT NOT NULL,
    pattern_json      TEXT NOT NULL,
    adaptations_json  TEXT NOT NULL,
    evidence_chain_json TEXT NOT NULL,
    created_at_epoch  REAL NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trg_learning_events_no_update
BEFORE UPDATE ON learning_events
BEGIN
    SELECT RAISE(ABORT, 'learning_events is append-only (UPDATE rejected)');
END;
CREATE TRIGGER IF NOT EXISTS trg_learning_events_no_delete
BEFORE DELETE ON learning_events
BEGIN
    SELECT RAISE(ABORT, 'learning_events is append-only (DELETE rejected)');
END;
"""


class LearningStore:
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
        d = event.to_dict() if hasattr(event, "to_dict") else event
        # adaptations stored as combined proposed/applied/rejected
        adaptations = {
            "proposed": d["adaptations_proposed"],
            "applied": d["adaptations_applied"],
            "rejected": d["adaptations_rejected"],
        }
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO learning_events "
                "(learning_event_id, outcome_id, context_id, pattern_json, "
                " adaptations_json, evidence_chain_json, created_at_epoch) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    d["learning_event_id"],
                    d["outcome_id"],
                    d["context_id"],
                    json.dumps(d["pattern_detected"], sort_keys=True, default=str),
                    json.dumps(adaptations, sort_keys=True, default=str),
                    json.dumps(d["evidence_chain"], sort_keys=True, default=str),
                    d["created_at_epoch"],
                ),
            )
        return d["learning_event_id"]

    def get(self, learning_event_id):
        row = self.conn.execute(
            "SELECT * FROM learning_events WHERE learning_event_id=?",
            (learning_event_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def all(self):
        rows = self.conn.execute(
            "SELECT * FROM learning_events ORDER BY created_at_epoch"
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def for_outcome(self, outcome_id):
        rows = self.conn.execute(
            "SELECT * FROM learning_events WHERE outcome_id=? ORDER BY created_at_epoch",
            (outcome_id,)).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _row_to_dict(self, row):
        return {
            "learning_event_id": row["learning_event_id"],
            "outcome_id": row["outcome_id"],
            "context_id": row["context_id"],
            "pattern_detected": json.loads(row["pattern_json"] or "null"),
            "adaptations": json.loads(row["adaptations_json"] or "{}"),
            "evidence_chain": json.loads(row["evidence_chain_json"] or "{}"),
            "created_at_epoch": row["created_at_epoch"],
        }
