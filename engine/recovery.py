"""Crash recovery / restart reconciliation (Layer 6C).

Deterministic restart hook: on (re)start, find every task still ``running``
(a crashed 6B coordinator) and mark its unfinished work:

* still-``pending`` steps   -> ``pending -> running -> skipped``
* still-``running`` steps   -> ``running -> skipped``
* the task itself           -> ``running -> failed``

Reason is always ``interrupted_restart``. All transitions use the 6A guarded
compare-and-set paths (``expected_status=``) and the same tables; no raw SQL,
no schema change, no re-execution, no auto-retry. Unclaimed ``planned`` tasks
and already-terminal tasks are left untouched. Idempotent by construction:
reconciling twice is the same as reconciling once.

This module never opens the production ``database/knowledge.db``; it only reads
and marks rows through the caller's :class:`EngineState`.
"""

REASON_INTERRUPTED_RESTART = "interrupted_restart"

_PENDING = "pending"
_RUNNING = "running"
_SKIPPED = "skipped"
_FAILED = "failed"


def _as_report(reconciled):
    return {"reconciled": list(reconciled), "count": len(reconciled)}


def reconcile(state):
    """Reconcile interrupted (``running``) tasks after a restart.

    Returns ``{"reconciled": [task_id, ...], "count": n}``. Never raises on
    unexpected row states; each unexpected case is recorded and left as-is.
    """
    reconciled = []

    for task_id in _running_task_ids(state):
        try:
            marked = _reconcile_task(state, task_id)
        except Exception:
            # Fail closed: never touch a task we could not reconcile cleanly.
            continue
        if marked:
            reconciled.append(task_id)

    return _as_report(reconciled)


def _running_task_ids(state):
    # Public read-only enumeration (Layer 6F); no private connection access.
    return [row["task_id"]
            for row in state.list_tasks(status=_RUNNING)]


def _reconcile_task(state, task_id):
    steps = state.list_task_steps(task_id)
    for step in steps:
        status = step["status"]
        if status == _PENDING:
            # pending -> running is the only legal edge from pending.
            hop = state.update_task_step_status(
                task_id, step["step_id"], _RUNNING,
                expected_status=_PENDING)
            if not hop.get("updated"):
                continue
            status = _RUNNING
        if status == _RUNNING:
            mark = state.update_task_step_status(
                task_id, step["step_id"], _SKIPPED,
                expected_status=_RUNNING)
            if not mark.get("updated"):
                continue
            # Persist the interrupted reason on the step (update_step_result
            # does not change status, so this is post-transition metadata).
            state.update_step_result(task_id, step["step_id"],
                                     result_json=None, error=REASON_INTERRUPTED_RESTART)

    final = state.update_task_status(
        task_id, _FAILED, expected_status=_RUNNING,
        status_reason=REASON_INTERRUPTED_RESTART)
    return bool(final.get("updated"))