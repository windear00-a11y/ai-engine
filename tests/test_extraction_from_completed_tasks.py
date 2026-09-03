"""Phase 3: correctly synthesizes experiences from completed journal tasks."""

import os
import sqlite3
import tempfile
import unittest

from intelligence.context.schema import ContextSnapshot
from intelligence.context.store import ContextStore
from intelligence.evidence import EvidenceType, record_evidence
from intelligence.evidence.store import EvidenceStore
from intelligence.experience import extract_experiences_from_completed_tasks
from intelligence.outcome import record_outcome
from intelligence.outcome.store import OutcomeStore
from intelligence.outcome.types import OutcomeClassification


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
            status TEXT CHECK (status IN ('pending','running','success',
                'failed','skipped','planned')),
            result_json TEXT, error TEXT, operation_id TEXT,
            started_at_epoch REAL, finished_at_epoch REAL, duration REAL,
            PRIMARY KEY (task_id, step_id)
        );
    """)
    conn.execute(
        "INSERT INTO tasks VALUES ('t1','{}','/w','completed',NULL,NULL,0,1,2,"
        "NULL,NULL,NULL)")
    conn.executemany(
        "INSERT INTO task_steps VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ('t1', 's1', 0, 'echo', '{}', 'success', None, None, None, 0, 1, 1),
            ('t1', 's2', 1, 'lint', '{}', 'failed', None, 'E302',
             None, 1, 2, 1),
        ],
    )
    conn.commit()
    conn.close()


class ExtractionFromCompletedTasksTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state_db = os.path.join(self.tmp.name, "engine_state.db")
        self.ev_db = os.path.join(self.tmp.name, "evidence.db")
        self.ctx_db = os.path.join(self.tmp.name, "context.db")
        build_journal(self.state_db)

        # context snapshot
        self.ctx_id = ContextSnapshot.build(
            system={"os": "linux", "python": "3.11"},
            project={"language": "python"}, task={"type": "bug_fix"},
            temporal={}, captured_at_epoch=0.0).context_id
        cstore = ContextStore(self.ctx_db)
        snap = ContextSnapshot.build(
            system={"os": "linux", "python": "3.11"},
            project={"language": "python"}, task={"type": "bug_fix"},
            temporal={}, captured_at_epoch=0.0)
        cstore.save(snap)
        cstore.close()

    def test_synthesizes_experience_from_completed_task(self):
        # record evidence + outcome for task t1 in the evidence db
        ev_store = EvidenceStore(self.ev_db)
        oc_store = OutcomeStore(self.ev_db)
        ev = record_evidence("obs", "lint must pass", self.ctx_id,
                             EvidenceType.FACT, store=ev_store)
        record_outcome("t1", self.ctx_id, OutcomeClassification.FAILURE,
                       [ev.evidence_id], store=oc_store)
        ev_store.close()
        oc_store.close()

        experiences = extract_experiences_from_completed_tasks(
            self.state_db, self.ev_db, self.ctx_db)
        self.assertEqual(len(experiences), 1)
        xp = experiences[0]
        self.assertEqual(xp.task_id, "t1")
        self.assertEqual(xp.context_id, self.ctx_id)
        self.assertEqual(xp.outcome_id is not None, True)
        self.assertEqual(set(xp.evidence_ids), {ev.evidence_id})
        self.assertEqual(xp.summary["outcome"], "failure")

    def test_extraction_deterministic(self):
        ev_store = EvidenceStore(self.ev_db)
        oc_store = OutcomeStore(self.ev_db)
        ev = record_evidence("obs", "lint must pass", self.ctx_id,
                             EvidenceType.FACT, store=ev_store)
        record_outcome("t1", self.ctx_id, OutcomeClassification.FAILURE,
                       [ev.evidence_id], store=oc_store)
        ev_store.close()
        oc_store.close()
        a = extract_experiences_from_completed_tasks(
            self.state_db, self.ev_db, self.ctx_db)
        b = extract_experiences_from_completed_tasks(
            self.state_db, self.ev_db, self.ctx_db)
        self.assertEqual([x.experience_id for x in a],
                         [x.experience_id for x in b])


if __name__ == "__main__":
    unittest.main()
