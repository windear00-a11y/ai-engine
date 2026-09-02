"""Persistent Execution Coordinator (Layer 6B).

Drives :class:`engine.task_engine.TaskEngine` as the PURE execution driver
while persisting every task/step transition and journal attachment through
:class:`tools.permissions.journal.EngineState`. One task identity runs from
planner output (``submit``) through claim, per-step execution, and terminal
task status.

Flow::

    submit(planner_output)  -> create_task(planned)
    run(task_id)            -> claim(owner)
                             -> per step:
                                  ensure_task_step(pending)
                                  update_task_step_status -> running
                                  resolve $ref inputs
                                  execute exactly one registered tool
                                  attach_operation (when a journal op exists)
                                  update_task_step_status -> success/failed
                                  update_step_result
                             -> update_task_result
                             -> update_task_status -> terminal

This module never opens the production ``database/knowledge.db`` and never
creates task rows outside the ``EngineState`` passed in. It does NOT
auto-approve, auto-run, or schedule anything: it executes exactly the task
handed to it and only through the gates the caller wired (deny-by-default
otherwise).
"""

import json
import time

_STATUS_COMPLETED = "completed"
_STATUS_FAILED = "failed"
_STATUS_INVALID = "invalid"
_STATUS_RUNNING = "running"

_STEP_PENDING = "pending"
_STEP_RUNNING = "running"
_STEP_SUCCESS = "success"
_STEP_FAILED = "failed"
_STEP_SKIPPED = "skipped"
_STEP_PLANNED = "planned"


def _as_fail_closed(task_id, error):
    """Report a run that failed closed without mutating anything."""
    return {
        "task_id": task_id, "status": _STATUS_INVALID, "claimed": False,
        "steps": [], "error": error, "ok": False,
    }


class PersistentCoordinator:
    """Durable coordinator: TaskEngine executes, EngineState persists.

    Parameters
    ----------
    state : EngineState
        The durable task/step store (6A). Never the production knowledge DB.
    engine : TaskEngine
        The pure execution driver. Its ``permissions`` wiring (ApprovalGate
        family) is exactly what gates every step during the run.
    owner_token : str
        Non-empty owner identity used for claiming and ownership assertions.
    """

    def __init__(self, state, engine, owner_token):
        self.state = state
        self.engine = engine
        if not isinstance(owner_token, str) or not owner_token:
            raise ValueError("owner_token must be a non-empty string")
        self.owner_token = owner_token

    # ------------------------------------------------------------------ #
    # submit: planner output -> durable planned task                      #
    # ------------------------------------------------------------------ #

    def submit(self, planner_output):
        """Persist ``planner_output`` as a ``planned`` task.

        Validation uses the engine's own rules so an invalid plan is rejected
        before anything is written.
        """
        if not isinstance(planner_output, dict):
            return {"ok": False, "task_id": None,
                    "error": "planner_output must be an object"}
        ok, errors = self.engine.validate_task(planner_output)
        if not ok:
            return {"ok": False,
                    "task_id": planner_output.get("id"),
                    "error": "; ".join(errors)}
        task_id = planner_output["id"]
        workspace_root = self.engine.coding.root
        res = self.state.create_task(
            task_id, planner_output, workspace_root, status="planned",
            planner_version=planner_output.get("planner_version"))
        if not res["ok"]:
            return {"ok": False, "task_id": task_id, "error": res["error"]}
        return {"ok": True, "task_id": task_id,
                "created": bool(res.get("created")), "status": "planned"}

    # ------------------------------------------------------------------ #
    # ownership                                                           #
    # ------------------------------------------------------------------ #

    def _assert_owner(self, task_id):
        row = self.state.get_task(task_id)
        if row is None:
            return {"ok": False, "error": "task not found"}
        if row["owner_token"] != self.owner_token:
            return {"ok": False,
                    "error": f"task {task_id!r} is owned by another worker"}
        return {"ok": True, "task": row}

    def _claim(self, task_id):
        claimed = self.state.claim_task(task_id, self.owner_token)
        if not claimed["ok"] or claimed.get("claimed") is not True:
            return {"ok": False, "error": claimed.get("error") or (
                "task could not be claimed")}
        return {"ok": True}

    # ------------------------------------------------------------------ #
    # one persisted step                                                  #
    # ------------------------------------------------------------------ #

    def _run_one(self, task_id, task, steps, store, i, dry_run,
                 planned_actions):
        step = steps[i]
        step_id = step["id"]
        tool = step.get("tool")
        raw_inputs = step.get("inputs", {}) or {}

        # Dry-run mutating tool: write-ahead intent is the planned step
        # itself (never executed, so never pending/running). TASK_STEP_STATUSES
        # allows a direct "planned" row; TASK_STEP_TRANSITIONS forbids
        # running->planned, so only this path is legal.
        if dry_run and tool in self.engine.MUTATING:
            self.state.ensure_task_step(
                task_id, step_id, i, tool, raw_inputs, status=_STEP_PLANNED)
            rec = self.engine._run_step(step, store, dry_run, planned_actions)
            store[step_id] = rec
            self._write_step_result(task_id, step_id, rec)
            return rec

        # Write-ahead intent, then advance pending -> running -> execute.
        self.state.ensure_task_step(task_id, step_id, i, tool, raw_inputs,
                                    status=_STEP_PENDING)
        self.state.update_task_step_status(
            task_id, step_id, _STEP_RUNNING, expected_status=_STEP_PENDING)

        rec = self.engine._run_step(step, store, dry_run, planned_actions)
        store[step_id] = rec

        status = rec["status"]
        if status == _STEP_SUCCESS:
            self.state.update_task_step_status(
                task_id, step_id, _STEP_SUCCESS, expected_status=_STEP_RUNNING)
        elif status == _STEP_FAILED:
            self.state.update_task_step_status(
                task_id, step_id, _STEP_FAILED, expected_status=_STEP_RUNNING)
        # Any other status (never produced by _run_step outside dry-run
        # mutating tools) leaves the row "running"; 6C reconciles it. No
        # illegal transition is ever attempted.

        self._write_step_result(task_id, step_id, rec)
        self._attach_operation_for(task_id, step_id, rec)
        return rec

    def _write_step_result(self, task_id, step_id, rec):
        self.state.update_step_result(
            task_id, step_id, result_json=rec["result"],
            error=rec.get("error"),
            started_at_epoch=rec.get("started_at"),
            finished_at_epoch=rec.get("finished_at"),
            duration=rec.get("duration"))

    def _attach_operation_for(self, task_id, step_id, rec):
        if not isinstance(rec["result"], dict):
            return None
        op_id = rec["result"].get("operation_id")
        if not isinstance(op_id, str) or not op_id:
            return None
        return self.state.attach_operation(task_id, step_id, op_id)

    def _persist_skipped(self, task_id, step, idx, error):
        self.state.ensure_task_step(
            task_id, step["id"], idx, step.get("tool"),
            step.get("inputs", {}) or {}, status=_STEP_SKIPPED,
            error=error)

    def _skip_records(self, task, steps, from_idx, error):
        """Mirror TaskEngine._record for steps that never run."""
        records = []
        for j in range(from_idx, len(steps)):
            step = steps[j]
            records.append({
                "task_id": task["id"],
                "id": step["id"],
                "tool": step.get("tool"),
                "raw_inputs": step.get("inputs", {}),
                "inputs": step.get("inputs", {}),
                "status": _STEP_SKIPPED,
                "result": None,
                "error": error,
                "started_at": None,
                "finished_at": None,
                "duration": None,
            })
        return records

    # ------------------------------------------------------------------ #
    # recovery                                                           #
    # ------------------------------------------------------------------ #

    def reconcile(self):
        """Delegate to :mod:`engine.recovery` on this coordinator's store."""
        from engine.recovery import reconcile
        return reconcile(self.state)

    # ------------------------------------------------------------------ #
    # run                                                                 #
    # ------------------------------------------------------------------ #

    def run(self, task_id):
        """Execute a claimed task through the engine, persisting per step.

        Mutates only the caller's EngineState. The transcript mirrors
        ``TaskEngine.run_task`` for the equivalent non-persisted task.
        """
        claim = self._claim(task_id)
        if not claim["ok"]:
            return _as_fail_closed(task_id, claim["error"])

        owned = self._assert_owner(task_id)
        if not owned["ok"]:
            # Claimed but ownership mismatch: mark it invalid, fail closed.
            self.state.update_task_status(task_id, _STATUS_INVALID,
                                          status_reason=owned["error"])
            return _as_fail_closed(task_id, owned["error"])

        try:
            task = json.loads(owned["task"]["task_json"])
        except Exception as e:
            return self._fail_task(task_id,
                                   f"stored task_json is corrupt: {e}")

        ok, errors = self.engine.validate_task(task)
        if not ok:
            return self._fail_task(task_id, "; ".join(errors))

        if not isinstance(task.get("steps"), list):
            return self._fail_task(task_id, "task.steps must be a list")

        self.engine._current_task_id = task["id"]
        steps = task["steps"]
        dry_run = bool(task.get("dry_run", False))
        stop_on_error = bool(task.get("stop_on_error", True))
        max_duration = task.get("max_duration",
                                self.engine.default_max_duration)

        records = []
        store = {}
        step_errors = []
        planned_actions = []
        overall = _STATUS_COMPLETED
        started = time.time()

        i = 0
        while i < len(steps) and time.time() - started <= max_duration:
            rec = self._run_one(task_id, task, steps, store, i, dry_run,
                                planned_actions)
            store[steps[i]["id"]] = rec
            records.append(rec)
            step = steps[i]

            if rec["status"] == _STEP_FAILED:
                step_errors.append({"step_id": step["id"],
                                    "error": rec["error"]})
                if stop_on_error:
                    overall = _STATUS_FAILED
                    skipped = self._skip_records(
                        task, steps, i + 1,
                        "skipped due to earlier step failure")
                    records.extend(skipped)
                    for j in range(i + 1, len(steps)):
                        step_errors.append(
                            {"step_id": steps[j]["id"],
                             "error": "skipped due to earlier step failure"})
                        self._persist_skipped(
                            task_id, steps[j], j,
                            "skipped due to earlier step failure")
                    i = len(steps)
                    break
                overall = "completed_with_errors"
            i += 1

        if i < len(steps):  # duration limit reached
            skipped = self._skip_records(
                task, steps, i, "duration limit exceeded")
            records.extend(skipped)
            for j in range(i, len(steps)):
                step_errors.append({"step_id": steps[j]["id"],
                                    "error": "duration limit exceeded"})
                self._persist_skipped(task_id, steps[j], j,
                                      "duration limit exceeded")
            if overall == _STATUS_COMPLETED:
                overall = _STATUS_FAILED

        if dry_run and overall == _STATUS_COMPLETED:
            overall = "planned"
        elif dry_run and overall == "completed_with_errors":
            overall = "planned_with_errors"

        finished = time.time()
        terminal = _map_terminal(overall)
        result = {
            "task_id": task["id"],
            "description": task.get("description"),
            "status": overall,
            "dry_run": dry_run,
            "planned": dry_run,
            "planned_actions": planned_actions,
            "step_count": len(records),
            "steps": records,
            "audit": records,
            "errors": step_errors,
            "started_at": started,
            "finished_at": finished,
            "duration": finished - started,
        }
        trickle = None if terminal == _STATUS_COMPLETED else overall
        self.state.update_task_result(task["id"], result,
                                      planner_version=task.get(
                                          "planner_version"))
        self.state.update_task_status(task["id"], terminal,
                                      expected_status=_STATUS_RUNNING,
                                      status_reason=trickle)

        return {
            "task_id": task["id"],
            "status": overall,
            "dry_run": dry_run,
            "planned": dry_run,
            "planned_actions": planned_actions,
            "step_count": len(records),
            "steps": records,
            "errors": step_errors,
            "claimed": True,
            "started_at": started,
            "finished_at": finished,
            "duration": finished - started,
            "ok": terminal == _STATUS_COMPLETED,
        }

    def _fail_task(self, task_id, reason):
        self.state.update_task_status(task_id, _STATUS_INVALID,
                                      expected_status=_STATUS_RUNNING,
                                      status_reason=reason)
        return {"task_id": task_id, "status": _STATUS_INVALID,
                "claimed": True, "steps": [], "error": reason, "ok": False}


def _map_terminal(overall):
    if overall in (_STATUS_COMPLETED, "planned", "completed_with_errors",
                   "planned_with_errors"):
        return _STATUS_COMPLETED
    if overall == _STATUS_FAILED:
        return _STATUS_FAILED
    return _STATUS_INVALID