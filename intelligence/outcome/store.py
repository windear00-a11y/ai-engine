"""Outcome and verification persistence (outcomes table, append-only) (Phase 2).

The ``outcomes`` table lives in ``database/evidence.db`` alongside evidence::

    outcome_id                  TEXT PRIMARY KEY
    plan_id                     TEXT
    context_id                  TEXT
    classification              TEXT
    verification_evidence_ids_json TEXT
    created_at_epoch            REAL

Outcomes are append-only (triggers reject UPDATE/DELETE). Verification is
enforced at the recorder layer (an outcome cannot be recorded without at
least one verification evidence id).
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
CREATE TABLE IF NOT EXISTS outcomes (
    outcome_id                  TEXT PRIMARY KEY,
    plan_id                     TEXT,
    context_id                  TEXT,
    classification              TEXT,
    verification_evidence_ids_json TEXT,
    created_at_epoch            REAL
);

CREATE TRIGGER IF NOT EXISTS trg_outcomes_no_update
BEFORE UPDATE ON outcomes
BEGIN
    SELECT RAISE(ABORT, 'outcomes is append-only (UPDATE rejected)');
END;

CREATE TRIGGER IF NOT EXISTS trg_outcomes_no_delete
BEFORE DELETE ON outcomes
BEGIN
    SELECT RAISE(ABORT, 'outcomes is append-only (DELETE rejected)');
END;
"""


class OutcomeStore:
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

    def save(self, outcome):
        try:
            with self.conn:
                self.conn.execute(
                    "INSERT OR ignore INTO outcomes "
                    "(outcome_id, plan_id, context_id, classification, "
                    " verification_evidence_ids_json, created_at_epoch) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        outcome.outcome_id,
                        outcome.plan_id,
                        outcome.context_id,
                        outcome.classification.value,
                        json.dumps(list(outcome.verification_evidence_ids),
                                   sort_keys=True),
                        outcome.created_at_epoch,
                    ),
                )
        except sqlite3.Error:
            raise
        return outcome.outcome_id

    def get(self, outcome_id):
        row = self.conn.execute(
            "SELECT * FROM outcomes WHERE outcome_id=?",
            (outcome_id,)).fetchone()
        return self._to_outcome(row) if row else None

    def for_plan(self, plan_id):
        rows = self.conn.execute(
            "SELECT * FROM outcomes WHERE plan_id=? ORDER BY created_at_epoch",
            (plan_id,)).fetchall()
        return [self._to_outcome(r) for r in rows]

    def all(self):
        rows = self.conn.execute(
            "SELECT * FROM outcomes ORDER BY created_at_epoch").fetchall()
        return [self._to_outcome(r) for r in rows]

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]

    def _to_outcome(self, row):
        from intelligence.outcome.schema import Outcome
        from intelligence.outcome.types import OutcomeClassification
        return Outcome(
            outcome_id=row["outcome_id"],
            plan_id=row["plan_id"],
            context_id=row["context_id"],
            classification=OutcomeClassification(row["classification"]),
            verification_evidence_ids=tuple(
                json.loads(row["verification_evidence_ids_json"] or "[]")),
            created_at_epoch=row["created_at_epoch"],
        )
