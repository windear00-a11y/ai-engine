"""Decision audit trail queries (Phase 7, D8).

Every decision -- including guidance and blocked decisions -- is recorded so
the audit trail is complete and queryable. These helpers expose the stored
decisions via the append-only DecisionStore.
"""

from .store import DecisionStore


def get_decision(decision_id, store=None):
    s = store or DecisionStore()
    close = store is None
    try:
        return s.get(decision_id)
    finally:
        if close:
            s.close()


def decisions_for_task(task_id, store=None):
    s = store or DecisionStore()
    close = store is None
    try:
        return s.list_for_task(task_id)
    finally:
        if close:
            s.close()


def decision_audit_summary(limit=100, store=None):
    s = store or DecisionStore()
    close = store is None
    try:
        return s.all(limit=limit)
    finally:
        if close:
            s.close()
