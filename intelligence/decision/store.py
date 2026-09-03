"""Decision persistence (Phase 7).

Decisions are immutable records stored in ``database/evidence.db`` in an
append-only ``decisions`` table. Every decision -- including ones that request
human guidance or are blocked by policy -- is recorded so the audit trail
(D8) is complete and queryable. The decisions table is additively created and
idempotent; UPDATE/DELETE are rejected at the DB level.
"""

import json
import os
import sqlite3

from .schema import decision_to_dict

_DEFAULT_EVIDENCE_DB = None


def default_evidence_db_path():
    global _DEFAULT_EVIDENCE_DB
    if _DEFAULT_EVIDENCE_DB is None:
        _ROOT = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _DEFAULT_EVIDENCE_DB = os.path.join(_ROOT, "database", "evidence.db")
    return _DEFAULT_EVIDENCE_DB


_SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    decision_id          TEXT PRIMARY KEY,
    task_id              TEXT NOT NULL,
    task_type            TEXT NOT NULL,
    selected_strategy_id TEXT,
    reasoning_id         TEXT,
    confidence           REAL NOT NULL,
    rationale_json       TEXT NOT NULL,
    alternatives_json    TEXT NOT NULL,
    risk_level           TEXT NOT NULL,
    approval_required    INTEGER NOT NULL DEFAULT 0,
    status               TEXT NOT NULL,
    context_id           TEXT NOT NULL,
    kind                 TEXT,
    created_at_epoch     REAL NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_decisions_no_update
BEFORE UPDATE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decisions is append-only (UPDATE rejected)');
END;

CREATE TRIGGER IF NOT EXISTS trg_decisions_no_delete
BEFORE DELETE ON decisions
BEGIN
    SELECT RAISE(ABORT, 'decisions is append-only (DELETE rejected)');
END;
"""


class DecisionStore:
    """Append-only persistence for decision records."""

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

    def save(self, decision):
        d = decision_to_dict(decision)
        with self.conn:
            self.conn.execute(
                "INSERT OR ignore INTO decisions "
                "(decision_id, task_id, task_type, selected_strategy_id, "
                " reasoning_id, confidence, rationale_json, "
                " alternatives_json, risk_level, approval_required, status, "
                " context_id, kind, created_at_epoch) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    d["decision_id"],
                    d["task_id"],
                    d["task_type"],
                    d["selected_strategy_id"],
                    d["reasoning_id"],
                    d["confidence"],
                    json.dumps(d["rationale"], sort_keys=True, default=str),
                    json.dumps(d["alternatives_rejected"], sort_keys=True,
                               default=str),
                    d["risk_level"],
                    1 if d["approval_required"] else 0,
                    d["status"],
                    d["context_id"],
                    d.get("kind"),
                    d["created_at_epoch"],
                ),
            )
        return d["decision_id"]

    def get(self, decision_id):
        row = self.conn.execute(
            "SELECT * FROM decisions WHERE decision_id=?",
            (decision_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def list_for_task(self, task_id):
        rows = self.conn.execute(
            "SELECT * FROM decisions WHERE task_id=? "
            "ORDER BY created_at_epoch, decision_id",
            (task_id,)).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def all(self, limit=1000):
        rows = self.conn.execute(
            "SELECT * FROM decisions "
            "ORDER BY created_at_epoch, decision_id LIMIT ?",
            (int(limit),)).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def _row_to_dict(self, row):
        return {
            "decision_id": row["decision_id"],
            "task_id": row["task_id"],
            "task_type": row["task_type"],
            "selected_strategy_id": row["selected_strategy_id"],
            "reasoning_id": row["reasoning_id"],
            "confidence": row["confidence"],
            "rationale": json.loads(row["rationale_json"] or "{}"),
            "alternatives_rejected": json.loads(
                row["alternatives_json"] or "[]"),
            "risk_level": row["risk_level"],
            "approval_required": bool(row["approval_required"]),
            "status": row["status"],
            "context_id": row["context_id"],
            "kind": row["kind"],
            "created_at_epoch": row["created_at_epoch"],
        }
