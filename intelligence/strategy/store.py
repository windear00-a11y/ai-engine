"""Strategy persistence (SQLite) (Phase 4).

The ``strategies`` table lives in ``database/evidence.db`` alongside the
evidence and outcomes that validate strategies (resolved Decision 5 and the
Database Strategy table).

Unlike evidence/outcome/experience, a strategy's ``confidence``,
``superseded_by`` and ``deprecated`` may change. Every mutation is therefore
recorded as an append-only row in the ``strategy_audit`` table so learning is
auditable and reversible. Reads expose the audited, current state.
"""

import json
import os
import sqlite3

from ai_engine.paths import get_legacy_db_path
from .schema import Strategy

_DEFAULT_EVIDENCE_DB = None


def default_evidence_db_path():
    global _DEFAULT_EVIDENCE_DB
    if _DEFAULT_EVIDENCE_DB is None:
        _DEFAULT_EVIDENCE_DB = get_legacy_db_path("evidence.db")
    return _DEFAULT_EVIDENCE_DB


_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategies (
    strategy_id              TEXT PRIMARY KEY,
    name                     TEXT NOT NULL,
    description              TEXT NOT NULL,
    problem_class            TEXT NOT NULL,
    tool_sequence_json       TEXT NOT NULL,
    constraints_json         TEXT NOT NULL,
    confidence               REAL NOT NULL,
    context_restrictions_json TEXT NOT NULL,
    strategy_type            TEXT NOT NULL,
    superseded_by            TEXT,
    deprecated               INTEGER NOT NULL DEFAULT 0,
    created_at_epoch         REAL NOT NULL,
    updated_at_epoch         REAL NOT NULL
);

-- Append-only audit trail for every strategy mutation (confidence changes,
-- deprecation, supersession). Protected from UPDATE/DELETE at the DB level.
CREATE TABLE IF NOT EXISTS strategy_audit (
    audit_id        TEXT PRIMARY KEY,
    strategy_id     TEXT NOT NULL,
    field           TEXT NOT NULL,
    old_value_json  TEXT,
    new_value_json  TEXT,
    evidence_ids_json TEXT,
    note            TEXT,
    created_at_epoch REAL NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trg_strategy_audit_no_update
BEFORE UPDATE ON strategy_audit
BEGIN
    SELECT RAISE(ABORT, 'strategy_audit is append-only (UPDATE rejected)');
END;

CREATE TRIGGER IF NOT EXISTS trg_strategy_audit_no_delete
BEFORE DELETE ON strategy_audit
BEGIN
    SELECT RAISE(ABORT, 'strategy_audit is append-only (DELETE rejected)');
END;
"""


class StrategyStore:
    """Read/write access to strategies with append-only audit."""

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

    # ---- registration / writes (audited) ----

    def save(self, strategy):
        """Insert a strategy (additive). Idempotent via INSERT OR IGNORE."""
        with self.conn:
            self.conn.execute(
                "INSERT OR ignore INTO strategies "
                "(strategy_id, name, description, problem_class, "
                " tool_sequence_json, constraints_json, confidence, "
                " context_restrictions_json, strategy_type, superseded_by, "
                " deprecated, created_at_epoch, updated_at_epoch) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                strategy.to_row(),
            )
        return strategy.strategy_id

    def update_confidence(self, strategy_id, new_confidence, evidence_ids,
                          delta, created_at_epoch, note=None):
        """Apply a confidence change and audit it atomically.

        ``new_confidence`` and ``delta`` are both recorded so the audit trail
        preserves a reproducible record of the change.
        """
        with self.conn:
            cur = self.conn.execute(
                "SELECT confidence FROM strategies WHERE strategy_id=?",
                (strategy_id,))
            row = cur.fetchone()
            if row is None:
                raise KeyError(strategy_id)
            old_confidence = row["confidence"]
            self.conn.execute(
                "UPDATE strategies SET confidence=?, updated_at_epoch=? "
                "WHERE strategy_id=?",
                (new_confidence, created_at_epoch, strategy_id),
            )
            self._append_audit(
                strategy_id, "confidence",
                {"confidence": old_confidence},
                {"confidence": new_confidence, "delta": delta},
                evidence_ids, created_at_epoch, note,
            )

    def set_deprecated(self, strategy_id, deprecated, created_at_epoch,
                       note=None):
        """Set (or clear) the deprecated flag and audit it."""
        with self.conn:
            cur = self.conn.execute(
                "SELECT deprecated FROM strategies WHERE strategy_id=?",
                (strategy_id,))
            row = cur.fetchone()
            if row is None:
                raise KeyError(strategy_id)
            old = bool(row["deprecated"])
            self.conn.execute(
                "UPDATE strategies SET deprecated=?, updated_at_epoch=? "
                "WHERE strategy_id=?",
                (1 if deprecated else 0, created_at_epoch, strategy_id),
            )
            self._append_audit(
                strategy_id, "deprecated",
                {"deprecated": old}, {"deprecated": bool(deprecated)},
                [], created_at_epoch, note,
            )

    def set_superseded_by(self, strategy_id, superseded_by, created_at_epoch,
                          note=None):
        """Record supersession and audit it."""
        with self.conn:
            cur = self.conn.execute(
                "SELECT superseded_by FROM strategies WHERE strategy_id=?",
                (strategy_id,))
            row = cur.fetchone()
            if row is None:
                raise KeyError(strategy_id)
            old = row["superseded_by"]
            self.conn.execute(
                "UPDATE strategies SET superseded_by=?, updated_at_epoch=? "
                "WHERE strategy_id=?",
                (superseded_by, created_at_epoch, strategy_id),
            )
            self._append_audit(
                strategy_id, "superseded_by",
                {"superseded_by": old}, {"superseded_by": superseded_by},
                [], created_at_epoch, note,
            )

    def _append_audit(self, strategy_id, field, old_value, new_value,
                      evidence_ids, created_at_epoch, note):
        import hashlib
        str_old = json.dumps(old_value, sort_keys=True, default=str)
        str_new = json.dumps(new_value, sort_keys=True, default=str)
        token = json.dumps(
            {"strategy_id": strategy_id, "field": field,
             "old": str_old, "new": str_new,
             "created_at_epoch": created_at_epoch},
            sort_keys=True, separators=(",", ":"))
        audit_id = "au_" + hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.conn.execute(
            "INSERT OR ignore INTO strategy_audit "
            "(audit_id, strategy_id, field, old_value_json, new_value_json, "
            " evidence_ids_json, note, created_at_epoch) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (
                audit_id, strategy_id, field, str_old, str_new,
                json.dumps(list(evidence_ids), separators=(",", ":")),
                note, created_at_epoch,
            ),
        )

    # ---- reads ----

    def get(self, strategy_id):
        row = self.conn.execute(
            "SELECT * FROM strategies WHERE strategy_id=?",
            (strategy_id,)).fetchone()
        if row is None:
            return None
        return Strategy.from_row(row)

    def list_by_problem_class(self, problem_class):
        rows = self.conn.execute(
            "SELECT * FROM strategies WHERE problem_class=? "
            "ORDER BY confidence DESC, name ASC",
            (problem_class,)).fetchall()
        return [Strategy.from_row(r) for r in rows]

    def all(self):
        rows = self.conn.execute(
            "SELECT * FROM strategies ORDER BY problem_class, name"
        ).fetchall()
        return [Strategy.from_row(r) for r in rows]

    def audit_trail(self, strategy_id=None):
        if strategy_id is None:
            rows = self.conn.execute(
                "SELECT * FROM strategy_audit ORDER BY created_at_epoch"
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM strategy_audit WHERE strategy_id=? "
                "ORDER BY created_at_epoch",
                (strategy_id,)).fetchall()
        return [dict(r) for r in rows]
