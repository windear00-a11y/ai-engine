"""Task-level rollback aggregation (Layer 6D).

Aggregates every journaled write operation attached to a persisted task
(via ``task_steps.operation_id``) and restores them in REVERSE journal order
using the existing Layer 5 :class:`~tools.permissions.rollback.RollbackExecutor`
+ approval-gated ``authorize_rollback`` path. On success the task terminalizes
``running -> rolled_back`` with aggregate evidence.

This is UNDO ONLY (no compensating writes). It never re-executes forward work,
never auto-retries, never opens the production ``knowledge.db``, and acts only
through the caller's :class:`EngineState` and ApprovalGate.
"""

REASON_ROLLED_BACK = "rollback_aggregated"

_STEP_ROLLED_BACK = "rolled_back"
_TASK_RUNNING = "running"

_ROLLBACK_OK = ("rolled_back", "rollback_ok", "restored", "deleted")


class TaskRollback:
    def __init__(self, path_policy, state, gate, approver=None):
        self.pp = path_policy
        self.state = state
        self.gate = gate
        self.approver = approver
        from tools.permissions.rollback import RollbackExecutor
        self.ex = RollbackExecutor(path_policy, state)

    # -- aggregation (pure read) -----------------------------------------

    def _operation_ids(self, task_id):
        steps = self.state.list_task_steps(task_id)
        ids = []
        for step in steps:
            op = step.get("operation_id")
            if isinstance(op, str) and op:
                ids.append(op)
        return ids

    def plan(self, task_id):
        """Read-only manifest of attachable write ops in rollback order."""
        ids = self._operation_ids(task_id)
        manifest = []
        safe = True
        conflicts = []
        for op_id in reversed(ids):
            p = self.ex.plan(op_id)
            manifest.append({
                "operation_id": op_id,
                "not_found": p.get("not_found", False),
                "safe": p.get("safe", False),
                "action": [r["action"] for r in p.get("rows", [])],
                "summary": p.get("summary", ""),
            })
            if not p.get("safe", False):
                safe = False
                conflicts.extend(p.get("conflicts", []))
        return {
            "task_id": task_id,
            "rollback_order": ids[::-1],
            "operations": manifest,
            "conflicts": conflicts,
            "safe": safe,
            "count": len(ids),
        }

    # -- execution -------------------------------------------------------

    def execute(self, task_id):
        """Roll back every attached operation (reverse order), aggregate
        evidence, and terminalize the task. Fail closed on any problem."""
        plan = self.plan(task_id)
        if plan["count"] == 0:
            return self._finalize(task_id, plan, [])

        if not plan["safe"]:
            return {"ok": False, "task_id": task_id,
                    "error": "rollback conflicts detected; nothing rolled "
                             f"back: {plan['conflicts']}"}

        evidence = []
        for op_id in plan["rollback_order"]:
            allowed, _d, err = self.gate.authorize_rollback(
                op_id, self.ex.resolve_fn, confirm=False)
            if not allowed:
                return {"ok": False, "task_id": task_id,
                        "error": f"authorization denied for {op_id}: {err}",
                        "evidence": evidence,
                        "rolled_back_operations":
                        [e["operation_id"] for e in evidence]}
            res = self.ex.execute(op_id, confirm=False)
            evidence.append({"operation_id": op_id, **res})
            if res.get("result") not in _ROLLBACK_OK:
                # Operationally failed (e.g. TOCTOU conflict / I/O). Stop,
                # stay non-terminal so an operator decides the next move.
                return {"ok": False, "task_id": task_id,
                        "error": f"rollback failed for {op_id}: "
                                 f"{res.get('error') or res.get('result')}",
                        "evidence": evidence,
                        "rolled_back_operations":
                        [e["operation_id"] for e in evidence]}

        return self._finalize(task_id, plan, evidence)

    def _finalize(self, task_id, plan, evidence):
        result = {
            "task_id": task_id,
            "rolled_back_operations": [e["operation_id"]
                                       for e in evidence],
            "rollback_count": len(evidence),
            "evidence": evidence,
            "manifest": plan,
        }
        self.state.update_task_result(task_id, result)
        final = self.state.update_task_status(
            task_id, _STEP_ROLLED_BACK, expected_status=_TASK_RUNNING,
            status_reason=REASON_ROLLED_BACK)
        ok = bool(final.get("updated"))
        current = None
        if not ok:
            task = self.state.get_task(task_id)
            current = task.get("status") if task else None
        return {
            "ok": ok,
            "task_id": task_id,
            "status": _STEP_ROLLED_BACK if ok else current,
            "rollback_count": len(evidence),
            "rolled_back_operations": [e["operation_id"] for e in evidence],
            "evidence": evidence,
            "error": None if ok else final.get("error")
                     or "task could not be marked rolled_back",
        }