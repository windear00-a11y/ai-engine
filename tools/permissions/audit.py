"""Audit log convenience API over the engine state DB.

The persisted audit records live in ``database/engine_state.db`` (the same
single state database that holds the journal). This module provides a thin,
deterministic-friendly query layer on top of
:class:`tools.permissions.journal.EngineState`.

Timestamps are stored for human inspection but are never used in decisions,
sequence ids, or checksums.
"""

from tools.permissions.journal import EngineState, deterministic_id


class AuditLog:
    """High-level audit writer/reader using EngineState.

    ``state`` may be shared with an approval gate so one DB file holds both
    journal and audit records. Tests inject a temporary path.
    """

    def __init__(self, state=None, db_path=None):
        if state is not None:
            self.state = state
        else:
            self.state = EngineState(db_path=db_path)

    def record(self, operation_id, domain, target, permission, decision,
               status="proposed", approval_id=None, result=None, error=None,
               checksum_ref=None):
        return self.state.add_audit(
            operation_id=operation_id, domain=domain, target=target,
            permission=permission, decision=decision, status=status,
            approval_id=approval_id, result=result, error=error,
            checksum_ref=checksum_ref)

    def for_operation(self, operation_id, limit=1000):
        return self.state.audit_records(operation_id=operation_id, limit=limit)

    def recent(self, limit=1000):
        return self.state.audit_records(limit=limit)

    def count(self):
        return self.state.audit_count()
