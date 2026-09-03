"""Experience persistence (SQLite, append-only) (Phase 3).

``database/experience.db`` stores synthesized experience records::

    experience_id       TEXT PRIMARY KEY
    task_id             TEXT
    task_type           TEXT
    domain              TEXT
    context_id          TEXT
    strategy_id         TEXT
    outcome_id          TEXT
    evidence_ids_json   TEXT
    summary_json        TEXT
    synthesized_at_epoch REAL

Append-only: only INSERT and SELECT are exposed; triggers reject UPDATE and
DELETE.
"""

import json
import os
import sqlite3

_DEFAULT_EXPERIENCE_DB = None


def default_experience_db_path():
    global _DEFAULT_EXPERIENCE_DB
    if _DEFAULT_EXPERIENCE_DB is None:
        _ROOT = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        _DEFAULT_EXPERIENCE_DB = os.path.join(_ROOT, "database",
                                              "experience.db")
    return _DEFAULT_EXPERIENCE_DB


_SCHEMA = """
CREATE TABLE IF NOT EXISTS experience (
    experience_id        TEXT PRIMARY KEY,
    task_id              TEXT,
    task_type            TEXT,
    domain               TEXT,
    context_id           TEXT,
    strategy_id          TEXT,
    outcome_id           TEXT,
    evidence_ids_json    TEXT,
    summary_json         TEXT,
    synthesized_at_epoch REAL
);

CREATE TRIGGER IF NOT EXISTS trg_experience_no_update
BEFORE UPDATE ON experience
BEGIN
    SELECT RAISE(ABORT, 'experience is append-only (UPDATE rejected)');
END;

CREATE TRIGGER IF NOT EXISTS trg_experience_no_delete
BEFORE DELETE ON experience
BEGIN
    SELECT RAISE(ABORT, 'experience is append-only (DELETE rejected)');
END;
"""


class ExperienceStore:
    def __init__(self, db_path=None, check_same_thread=True):
        self.db_path = db_path or default_experience_db_path()
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
                    "INSERT OR ignore INTO experience "
                    "(experience_id, task_id, task_type, domain, context_id, "
                    " strategy_id, outcome_id, evidence_ids_json, "
                    " summary_json, synthesized_at_epoch) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.experience_id,
                        record.task_id,
                        record.task_type,
                        record.domain,
                        record.context_id,
                        record.strategy_id,
                        record.outcome_id,
                        json.dumps(list(record.evidence_ids), sort_keys=True),
                        json.dumps(record.summary, sort_keys=True,
                                   default=str),
                        record.synthesized_at_epoch,
                    ),
                )
        except sqlite3.Error:
            raise
        return record.experience_id

    def get(self, experience_id):
        row = self.conn.execute(
            "SELECT * FROM experience WHERE experience_id=?",
            (experience_id,)).fetchone()
        return self._to_record(row) if row else None

    def for_task_type(self, task_type, context_id=None):
        if context_id is not None:
            rows = self.conn.execute(
                "SELECT * FROM experience WHERE task_type=? AND context_id=? "
                "ORDER BY synthesized_at_epoch",
                (task_type, context_id)).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM experience WHERE task_type=? "
                "ORDER BY synthesized_at_epoch",
                (task_type,)).fetchall()
        return [self._to_record(r) for r in rows]

    def for_strategy(self, strategy_id):
        rows = self.conn.execute(
            "SELECT * FROM experience WHERE strategy_id=? "
            "ORDER BY synthesized_at_epoch", (strategy_id,)).fetchall()
        return [self._to_record(r) for r in rows]

    def all(self):
        rows = self.conn.execute(
            "SELECT * FROM experience ORDER BY synthesized_at_epoch").fetchall()
        return [self._to_record(r) for r in rows]

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM experience").fetchone()[0]

    def _to_record(self, row):
        from intelligence.experience.schema import ExperienceRecord
        return ExperienceRecord(
            experience_id=row["experience_id"],
            task_id=row["task_id"],
            task_type=row["task_type"],
            domain=row["domain"],
            context_id=row["context_id"],
            strategy_id=row["strategy_id"],
            outcome_id=row["outcome_id"],
            evidence_ids=tuple(json.loads(row["evidence_ids_json"] or "[]")),
            summary=json.loads(row["summary_json"] or "{}"),
            synthesized_at_epoch=row["synthesized_at_epoch"],
        )
