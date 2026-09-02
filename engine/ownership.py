"""Owner-scoped deny-by-default guard over the persisted task layer (Layer 6E).

Ownership lives on the task row (``owner_token``, assigned atomically by
:meth:`EngineState.claim_task`). :class:`OwnerScope` re-exposes the read/write
surface of :class:`~tools.permissions.journal.EngineState` so that EVERY call
first verifies the caller token owns the row: any mismatch fails closed with
no read leakage and no side effects.

Design notes:
* Denied reads return the same shapes a missing row would (``None`` / ``[]``)
  so existence is never disclosed to a non-owner beyond a deny boolean.
* Denied writes return ``{"ok": False, "denied": True, ...}`` and never touch
  the wrapped state.
* The scope never opens a database by itself; it only delegates to the
  wrapped :class:`EngineState`.
* Recovery (:mod:`engine.recovery`) stays a system-level scan on raw
  :class:`EngineState` by design and is NOT routed here.
"""


class OwnerScope:
    def __init__(self, state, owner_token):
        self.state = state
        if not isinstance(owner_token, str) or not owner_token:
            raise ValueError("owner_token must be a non-empty string")
        self.owner_token = owner_token

    # -- ownership --------------------------------------------------------

    def owns(self, task_id):
        """True only when the row exists and is bound to this scope's owner."""
        if self.state is None:
            return False
        row = self.state.get_task(task_id)
        return bool(row) and row["owner_token"] == self.owner_token

    def _deny(self, op, task_id):
        return {"ok": False, "denied": True, "op": op, "task_id": task_id,
                "error": "not owner"}

    # -- reads (denied -> harmless shapes, no leakage) --------------------

    def get_task(self, task_id):
        if not self.owns(task_id):
            return None
        return self.state.get_task(task_id)

    def list_task_steps(self, task_id):
        if not self.owns(task_id):
            return []
        return self.state.list_task_steps(task_id)

    def get_task_step(self, task_id, step_id):
        if not self.owns(task_id):
            return None
        return self.state.get_task_step(task_id, step_id)

    # -- writes (denied -> no side effects) -------------------------------

    def ensure_task_step(self, task_id, step_id, idx, tool, inputs_json,
                         **kw):
        if not self.owns(task_id):
            return self._deny("ensure_task_step", task_id)
        return self.state.ensure_task_step(task_id, step_id, idx, tool,
                                           inputs_json, **kw)

    def update_task_step_status(self, task_id, step_id, status, **kw):
        if not self.owns(task_id):
            return self._deny("update_task_step_status", task_id)
        return self.state.update_task_step_status(task_id, step_id, status,
                                                  **kw)

    def update_step_result(self, task_id, step_id, **kw):
        if not self.owns(task_id):
            return self._deny("update_step_result", task_id)
        return self.state.update_step_result(task_id, step_id, **kw)

    def attach_operation(self, task_id, step_id, operation_id):
        if not self.owns(task_id):
            return self._deny("attach_operation", task_id)
        return self.state.attach_operation(task_id, step_id, operation_id)

    def update_task_result(self, task_id, result_json, **kw):
        if not self.owns(task_id):
            return self._deny("update_task_result", task_id)
        return self.state.update_task_result(task_id, result_json, **kw)

    def update_task_status(self, task_id, status, **kw):
        if not self.owns(task_id):
            return self._deny("update_task_status", task_id)
        return self.state.update_task_status(task_id, status, **kw)