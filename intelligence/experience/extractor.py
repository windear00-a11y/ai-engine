"""Experience synthesis from execution journals + outcomes + evidence (Phase 3).

``synthesize_experience`` builds a deterministic, interpreted
:class:`ExperienceRecord` from a task, its context, its outcome, and the
evidence chain that supports the outcome, persisting it to experience.db.

``extract_experiences_from_completed_tasks`` reads completed/failed tasks
from an ``engine_state.db`` journal (read-only), pairs them with their
verified outcomes (read from ``evidence.db`` outcomes), pulls the context
snapshot (from ``context.db``), and synthesizes one experience per linked
outcome. It returns the derived records (read-only over the three source
databases; it does not persist them).

An experience is the *interpreted* summary. It deliberately does NOT embed
raw step inputs/results. Only the interpreted ``summary`` is kept.
"""

import json
import os
import sqlite3
import time

from intelligence.experience.schema import (
    ExperienceRecord,
    derive_experience_id,
)
from intelligence.experience.store import ExperienceStore
from intelligence.experience.types import TASK_UNKNOWN
from intelligence.outcome.store import OutcomeStore

_TERMINAL_TASK_STATUSES = ("completed", "failed")


def synthesize_experience(task_id, context_id, outcome_id, evidence_ids,
                          task_type="", domain="", strategy_id=None,
                          summary=None, store=None, synthesized_at_epoch=None):
    """Deterministically synthesize and persist an experience record.

    Parameters
    ----------
    context_id : str
        Must be non-empty (every experience references its context).
    outcome_id : str
        Must be non-empty (every experience references its outcome).

    Returns
    -------
    ExperienceRecord
    """
    if not context_id:
        raise ValueError("experience requires a non-empty context_id")
    if not outcome_id:
        raise ValueError("experience requires an outcome_id")
    if synthesized_at_epoch is None:
        synthesized_at_epoch = time.time()
    evidence_ids = [str(e) for e in (evidence_ids or [])]
    experience_id = derive_experience_id(
        task_id, context_id, outcome_id, evidence_ids, strategy_id)
    record = ExperienceRecord(
        experience_id=experience_id,
        task_id=task_id,
        task_type=task_type or TASK_UNKNOWN,
        domain=domain,
        context_id=context_id,
        outcome_id=outcome_id,
        evidence_ids=tuple(evidence_ids),
        strategy_id=strategy_id,
        summary=dict(summary or {}),
        synthesized_at_epoch=synthesized_at_epoch,
    )
    ctx_store = store or ExperienceStore()
    try:
        ctx_store.save(record)
    finally:
        if store is None:
            ctx_store.close()
    return record


def _build_record(task_id, context_id, outcome_id, evidence_ids,
                  task_type, domain, strategy_id, summary,
                  synthesized_at_epoch):
    evidence_ids = [str(e) for e in (evidence_ids or [])]
    experience_id = derive_experience_id(
        task_id, context_id, outcome_id, evidence_ids, strategy_id)
    return ExperienceRecord(
        experience_id=experience_id,
        task_id=task_id,
        task_type=task_type or TASK_UNKNOWN,
        domain=domain,
        context_id=context_id,
        outcome_id=outcome_id,
        evidence_ids=tuple(evidence_ids),
        strategy_id=strategy_id,
        summary=dict(summary or {}),
        synthesized_at_epoch=synthesized_at_epoch,
    )


def extract_experiences_from_completed_tasks(state_db_path, evidence_db_path,
                                             context_db_path, limit=100):
    """Read three databases (read-only) and return derived experience records.

    Records are returned, not persisted. Callers may persist individually via
    ``synthesize_experience`` if desired.
    """
    if not os.path.isfile(state_db_path):
        raise FileNotFoundError("journal db not found: %s" % state_db_path)
    if not os.path.isfile(evidence_db_path):
        raise FileNotFoundError("evidence db not found: %s" % evidence_db_path)

    context_index = _load_contexts(context_db_path)

    conn = sqlite3.connect("file:%s?mode=ro" % state_db_path, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tasks = conn.execute(
            "SELECT task_id, task_json, status FROM tasks "
            "ORDER BY finished_at_epoch").fetchall()
    except sqlite3.Error as exc:
        raise ValueError("could not read journal tasks: %s" % exc) from exc
    finally:
        conn.close()

    ev_store = OutcomeStore(evidence_db_path)
    try:
        outcomes_by_task = {}
        for row in ev_store.conn.execute(
                "SELECT * FROM outcomes ORDER BY created_at_epoch").fetchall():
            outcomes_by_task.setdefault(row["plan_id"], []).append(row)

        results = []
        for task in tasks:
            if task["status"] not in _TERMINAL_TASK_STATUSES:
                continue
            for oc_row in outcomes_by_task.get(task["task_id"], []):
                context_id = oc_row["context_id"]
                if not context_id:
                    continue  # experience requires context
                evidence_ids = _json_list(
                    oc_row["verification_evidence_ids_json"])
                oc_classification = oc_row["classification"]
                summary = {
                    "outcome": oc_classification,
                    "status": task["status"],
                    "text": "%s task %s" % (oc_classification,
                                            task["task_id"]),
                    "context_resolved": context_index.get(context_id)
                    is not None,
                }
                results.append(_build_record(
                    task_id=task["task_id"],
                    context_id=context_id,
                    outcome_id=oc_row["outcome_id"],
                    evidence_ids=evidence_ids,
                    task_type="",
                    domain="",
                    strategy_id=None,
                    summary=summary,
                    synthesized_at_epoch=oc_row["created_at_epoch"] or 0.0,
                ))
                if len(results) >= limit:
                    return results
        return results
    finally:
        ev_store.close()


def _load_contexts(context_db_path):
    """Load context snapshots as {context_id -> snapshot} (best effort)."""
    if not context_db_path or not os.path.isfile(context_db_path):
        return {}
    try:
        from intelligence.context.store import ContextStore
        store = ContextStore(context_db_path)
        try:
            return {s.context_id: s for s in store.all()}
        finally:
            store.close()
    except Exception:
        return {}


def _json_list(raw):
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return []
