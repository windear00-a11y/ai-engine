"""Extract outcomes from existing execution journal records (Phase 2).

``extract_outcomes_from_journal`` reads ``engine_state.db`` task_steps records
(read-only) and derives verified :class:`Outcome` values deterministically
from each step's status.

A step is "verified" (eligible to become an outcome) only when it has reached
a terminal classification (success/failed/skipped). Steps still
pending/running/planned are NOT outcomes yet and are skipped.

This extraction is read-only over the journal: it never writes to
engine_state.db. Derived outcomes are returned to the caller (they may then
be persisted to evidence.db via ``record_outcome`` if desired).
"""

import json
import os
import sqlite3

from intelligence.outcome.schema import Outcome, derive_outcome_id
from intelligence.outcome.types import OutcomeClassification

# Journal step status -> outcome classification (terminal statuses only).
_STATUS_TO_CLASSIFICATION = {
    "success": OutcomeClassification.SUCCESS,
    "failed": OutcomeClassification.FAILURE,
    "skipped": OutcomeClassification.BLOCKED,
}


def _journal_schema(db_path):
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def extract_outcomes_from_journal(state_db_path, limit=100):
    """Read task_steps from an engine_state.db and return derived Outcomes.

    Returns a list of up to ``limit`` :class:`Outcome` records, ordered by
    step finish time (oldest first). Read-only; raises on a missing/unreadable
    journal.
    """
    if not os.path.isfile(state_db_path):
        raise FileNotFoundError("journal db not found: %s" % state_db_path)
    conn = _journal_schema(state_db_path)
    try:
        rows = conn.execute(
            "SELECT task_id, step_id, tool, status, result_json, error, "
            "finished_at_epoch FROM task_steps "
            "ORDER BY finished_at_epoch"
        ).fetchall()
    except sqlite3.Error as exc:
        raise ValueError(
            "could not read journal task_steps (is this an engine_state.db?): "
            "%s" % exc
        ) from exc
    finally:
        conn.close()

    outcomes = []
    for row in rows:
        classification = _STATUS_TO_CLASSIFICATION.get(row["status"])
        if classification is None:
            continue  # not terminal -> not yet verified
        plan_id = row["task_id"]
        step_id = row["step_id"]
        # the journal step record is the verification source id
        verification_evidence_ids = ("journal:" + plan_id + ":" + step_id,)
        metadata = {
            "step_id": step_id,
            "tool": row["tool"],
            "journal_status": row["status"],
            "error": row["error"] or None,
        }
        outcome_id = derive_outcome_id(
            plan_id, None, classification, verification_evidence_ids, metadata)
        outcomes.append(Outcome(
            outcome_id=outcome_id,
            plan_id=plan_id,
            context_id=None,
            classification=classification,
            verification_evidence_ids=verification_evidence_ids,
            metadata=metadata,
            created_at_epoch=row["finished_at_epoch"] or 0.0,
        ))
        if len(outcomes) >= limit:
            break
    return outcomes
