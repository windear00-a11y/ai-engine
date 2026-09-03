"""Phase 2: outcomes are correctly extracted from engine_state.db records."""

import os
import sqlite3
import tempfile
import unittest

from intelligence.outcome import (
    OutcomeClassification,
    extract_outcomes_from_journal,
)
from intelligence.outcome.schema import derive_outcome_id


def build_journal(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE tasks (
            task_id TEXT PRIMARY KEY, task_json TEXT, workspace_root TEXT,
            status TEXT, status_reason TEXT, owner_token TEXT,
            created_at_epoch REAL, updated_at_epoch REAL,
            finished_at_epoch REAL, result_json TEXT, planner_version TEXT,
            proposal_ids_json TEXT
        );
        CREATE TABLE task_steps (
            task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
            step_id TEXT NOT NULL, idx INTEGER NOT NULL, tool TEXT NOT NULL,
            inputs_json TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending','running',
                'success','failed','skipped','planned')),
            result_json TEXT, error TEXT, operation_id TEXT,
            started_at_epoch REAL, finished_at_epoch REAL, duration REAL,
            PRIMARY KEY (task_id, step_id)
        );
    """)
    conn.execute(
        "INSERT INTO tasks VALUES ('t1','{}','/w','completed',NULL,NULL,"
        "0,1,2,NULL,NULL,NULL)")
    conn.executemany(
        "INSERT INTO task_steps VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ('t1', 's1', 0, 'echo', '{}', 'success',
             '{"ok": true}', None, None, 0, 1, 1),
            ('t1', 's2', 1, 'lint', '{}', 'failed', None, 'E302',
             None, 1, 2, 1),
            ('t1', 's3', 2, 'apply', '{}', 'skipped', None, None,
             None, 2, 3, 1),
            ('t1', 's4', 3, 'echo', '{}', 'running', None, None,
             None, 3, None, None),
        ],
    )
    conn.commit()
    conn.close()


class ExtractionFromJournalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.jdb = os.path.join(self.tmp.name, "engine_state.db")
        build_journal(self.jdb)

    def test_extracts_terminal_steps_only(self):
        outs = extract_outcomes_from_journal(self.jdb)
        # running step (s4) is not terminal -> excluded
        self.assertEqual(len(outs), 3)
        self.assertEqual(
            [o.classification for o in outs],
            [OutcomeClassification.SUCCESS,
             OutcomeClassification.FAILURE,
             OutcomeClassification.BLOCKED],
        )

    def test_outcome_ids_are_deterministic(self):
        a = extract_outcomes_from_journal(self.jdb)
        b = extract_outcomes_from_journal(self.jdb)
        self.assertEqual([o.outcome_id for o in a],
                         [o.outcome_id for o in b])

    def test_missing_journal_raises(self):
        with self.assertRaises(FileNotFoundError):
            extract_outcomes_from_journal(
                os.path.join(self.tmp.name, "nope.db"))

    def test_evidence_id_derivation_matches(self):
        outs = extract_outcomes_from_journal(self.jdb)
        first = outs[0]
        expected = derive_outcome_id(
            first.plan_id, None, first.classification,
            first.verification_evidence_ids, first.metadata)
        self.assertEqual(first.outcome_id, expected)


if __name__ == "__main__":
    unittest.main()
