"""Deterministic workflow automation over the coordinator (Layer 8A).

A bounded, selfishly-gated automation loop that drives the Layer 6b
``PersistentCoordinator`` with the SAME human approval gates as single tasks.

Design
------
* A :class:`WorkflowSpec` describes a bounded list of planner-valid task dicts
  (``items``) plus hard caps (``max_iterations``, ``max_duration``, optional
  ``git`` commit-on-verify).
* Each item passes through: dry-run plan -> operator approval -> apply ->
  verify -> (optional) approval-gated git commit. Every phase is persisted
  through the coordinator / EngineState / ApprovalGate audit path.
* Nobody runs without the gate: the workflow-level operator decision AND the
  per-tool ApprovalGate both must allow a mutating item or zero mutations
  happen (tree + git untouched, denial journaled).
* Fail-closed: denial at any gate -> that item is ``planned_denied`` and the
  loop simply moves on (or stops); verify failure after a mutation -> 6D
  rollback restores the workspace; iteration/duration caps stop the loop.
* There is no daemon, no unstructured shell, no concurrency, no push, no AI.

This module never opens the production ``database/knowledge.db``.
"""

import time

from dataclasses import dataclass, field

# -- item lifecycle -----------------------------------------------------------------

ST_PLANNED = "planned"
ST_PLANNED_DENIED = "planned_denied"
ST_APPLYING = "applying"
ST_VERIFYING = "verifying"
ST_COMPLETED = "completed"
ST_ROLLED_BACK = "rolled_back"
ST_FAILED = "failed"
ST_CAPPED = "capped"
ST_SKIPPED = "skipped"

_ITEM_STATES = (
    ST_PLANNED, ST_PLANNED_DENIED, ST_APPLYING, ST_VERIFYING,
    ST_COMPLETED, ST_ROLLED_BACK, ST_FAILED, ST_CAPPED, ST_SKIPPED,
)


def _default_operator(item_id):
    return False


@dataclass(frozen=True)
class WorkflowSpec:
    """A bounded automation run.

    ``items`` is a tuple of planner-valid task dicts (the same shape accepted by
    ``PersistentCoordinator.submit`` / ``TaskEngine.validate_task``). ``git``
    optionally describes an approval-gated commit-on-verify::

        {"message": "subject", "files": ["rel/path", ...]}

    The git stage/commit steps only run after apply + verify succeed AND the
    operator approved the item (git remains 7B-approval-gated).
    """
    id: str
    workspace_root: str
    items: tuple = field(default_factory=tuple)
    max_iterations: int = 8
    max_duration: float = 600.0
    verify_tool: str = "project.check"
    git: dict = field(default_factory=dict)


class WorkflowEngine:
    """Deterministic, operator-gated orchestration of a bounded item list.

    ``operator(item_id) -> bool`` is the workflow-level human gate (default:
    deny everything). The per-tool ``ApprovalGate`` inside the engine remains
    the final authority for each mutation.
    """

    def __init__(self, spec, engine, coordinator, operator=None):
        if not isinstance(spec.id, str) or not spec.id:
            raise ValueError("spec.id must be a non-empty string")
        if not isinstance(spec.items, (tuple, list)):
            raise ValueError("spec.items must be a sequence of task dicts")
        for it in spec.items:
            ok, errors = engine.validate_task(it)
            if not ok:
                raise ValueError("item validation failed: %s" % "; ".join(
                    errors))
        self.max_iterations = max(1, min(100, int(spec.max_iterations)))
        self.max_duration = float(spec.max_duration)
        self.spec = spec
        self.engine = engine
        self.coordinator = coordinator
        self._operator = operator if operator is not None else _default_operator
        self.started = time.time()
        self._state = {
            it["id"]: {"status": None, "decision": None, "planned": []}
            for it in spec.items
        }

    # -- helpers -------------------------------------------------------------------

    def item_status(self, item_id):
        rec = self._state.get(item_id)
        return rec["status"] if rec else None

    def item_decision(self, item_id):
        rec = self._state.get(item_id)
        return rec.get("decision") if rec else None

    def _set_status(self, item_id, status, reason=None):
        if status not in _ITEM_STATES:
            raise ValueError("unknown item status: %r" % status)
        if item_id in self._state:
            self._state[item_id]["status"] = status
            if reason is not None:
                self._state[item_id]["reason"] = reason

    def _find_task(self, item_id):
        return next((it for it in self.spec.items if it["id"] == item_id), {})

    def _audit(self, item_id, domain, target, permission, decision, status):
        state = getattr(self.engine.permissions, "state", None)
        if state is None or not hasattr(state, "add_audit"):
            return
        try:
            state.add_audit(
                operation_id="wf:%s:%s" % (self.spec.id, item_id),
                domain=domain, target=target, permission=permission,
                decision=decision, status=status, approval_id=status)
        except Exception:
            pass

    # -- planning ------------------------------------------------------------------

    def plan_item(self, item_id):
        """Dry-run an item (no mutation) and record its planned actions."""
        if item_id not in self._state:
            return {"ok": False, "item_id": item_id, "error": "unknown item"}
        task = self._find_task(item_id)
        clone = dict(task)
        clone["dry_run"] = True
        try:
            res = self.engine.run_task(clone)
        except Exception as e:
            self._set_status(item_id, ST_FAILED, reason=str(e))
            return {"ok": False, "item_id": item_id, "error": str(e)}
        actions = list(res.get("planned_actions") or [])
        self._state[item_id]["planned"] = actions
        self._set_status(item_id, ST_PLANNED)
        return {"ok": True, "item_id": item_id, "planned_actions": actions,
                "status": res.get("status"), "step_count": len(actions)}

    # -- approval ------------------------------------------------------------------

    def request_approval(self, item_id):
        """Present an item's planned mutation summary for operator approval."""
        if item_id not in self._state:
            return {"ok": False, "item_id": item_id, "error": "unknown item"}
        task = self._find_task(item_id)
        return {
            "ok": True, "item_id": item_id,
            "planned_actions": list(self._state[item_id]["planned"]),
            "step_count": len(task.get("steps", [])),
            "approval_token": "wfa:%s:%s" % (self.spec.id, item_id),
            "state": self.item_status(item_id),
        }

    def decide(self, item_id, approve_bool, operator="operator"):
        """Record the operator decision for an item (audited).

        Denial does NOT touch the item's persisted task; it only records the
        decision. ``apply_item`` refuses to run any denied item.
        """
        if item_id not in self._state:
            return {"ok": False, "item_id": item_id, "error": "unknown item"}
        decision = "approved" if approve_bool else "denied"
        self._state[item_id]["decision"] = decision
        self._audit(item_id, "workflow", item_id, "approve",
                    "allow" if approve_bool else "deny", decision)
        return {"ok": True, "item_id": item_id, "decision": decision,
                "operator": operator}

    # -- apply / verify / commit ---------------------------------------------------

    def apply_item(self, item_id):
        """Execute one item for real through the coordinator, gated.

        Refuses to run any item whose operator decision is not ``approved``, so
        a denied item never mutates the tree or git. On success it runs the
        read-only verify step; on verify failure it calls the 6D rollback.
        """
        if item_id not in self._state:
            return {"ok": False, "item_id": item_id, "error": "unknown item"}
        if self.item_decision(item_id) != "approved":
            self._set_status(item_id, ST_PLANNED_DENIED)
            return {"ok": False, "item_id": item_id,
                    "status": ST_PLANNED_DENIED,
                    "error": "item was not approved; no mutations applied"}

        task = self._find_task(item_id)
        self._set_status(item_id, ST_APPLYING)
        subm = self.coordinator.submit(task)
        if not subm["ok"]:
            self._set_status(item_id, ST_FAILED, reason=subm["error"])
            return {"ok": False, "item_id": item_id, "error": subm["error"]}
        run = self.coordinator.run(task["id"])
        if not run.get("ok"):
            self._set_status(item_id, ST_FAILED)
            return {"ok": False, "item_id": item_id,
                    "status": run.get("status"), "error": run.get("error")}

        self._set_status(item_id, ST_VERIFYING)
        verify_ok = self._verify(task["id"])
        if not verify_ok:
            rb = self.coordinator.rollback(task["id"])
            # An item task is already terminal after a completed apply, so the
            # 6D status terminalization (running -> rolled_back) is a no-op;
            # reversal is proven by the rollback having reversed real writes.
            conflicts = sum(len(e.get("conflicts") or [])
                            for e in rb.get("evidence") or [])
            reversed = bool(
                (rb.get("rollback_count") or 0) > 0 and conflicts == 0)
            self._set_status(
                item_id, ST_ROLLED_BACK if reversed else ST_FAILED)
            return {"ok": False, "item_id": item_id,
                    "status": self.item_status(item_id),
                    "verify": "failed", "rollback": rb}

        commit = None
        if self.spec.git:
            commit = self._commit_on_verify(task["id"])

        self._set_status(item_id, ST_COMPLETED)
        return {"ok": True, "item_id": item_id, "status": ST_COMPLETED,
                "verify": "passed", "commit": commit}

    def _verify(self, task_id):
        vtask = {"id": "%s:verify:task" % task_id, "stop_on_error": True,
                 "steps": [{"id": "v1", "tool": self.spec.verify_tool,
                            "inputs": {}}]}
        ok, errors = self.engine.validate_task(vtask)
        if not ok:
            return False
        try:
            sub = self.coordinator.submit(vtask)
            if not sub.get("ok"):
                return False
            res = self.coordinator.run(vtask["id"])
        except Exception:
            return False
        if not res.get("ok"):
            return False
        # The coordinator marks a completed step as success, so interpret the
        # verify tool's OWN success/exit indicator (e.g. project.check.success).
        steps = res.get("steps") or []
        for step in steps:
            if step.get("tool") != self.spec.verify_tool:
                continue
            result = step.get("result") or {}
            if "success" in result:
                return bool(result["success"])
        return True

    def _commit_on_verify(self, task_id):
        cfg = self.spec.git
        files = cfg.get("files") or []
        message = cfg.get("message") or ""
        steps = []
        if files:
            steps.append({"id": "g1", "tool": "git.stage",
                          "inputs": {"paths": list(files)}})
        if message:
            steps.append({"id": "g2", "tool": "git.commit",
                          "inputs": {"message": message}})
        if not steps:
            return None
        ctask = {"id": "%s:git:task" % task_id, "stop_on_error": True,
                 "steps": steps}
        ok, errors = self.engine.validate_task(ctask)
        if not ok:
            return {"ok": False, "error": "; ".join(errors)}
        try:
            sub = self.coordinator.submit(ctask)
            if not sub.get("ok"):
                return {"ok": False, "error": sub.get("error")}
            return self.coordinator.run(ctask["id"])
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # -- the loop ------------------------------------------------------------------

    def run(self, operator=None):
        """Execute the bounded loop over the item list.

        Uses ``operator`` (or the constructor callback) as the per-item human
        gate inside the loop. Honors ``max_iterations`` and ``max_duration``;
        on cap the remaining items are left untouched and reported ``capped``.
        """
        op = operator if operator is not None else self._operator
        summary = {"spec_id": self.spec.id, "items": [], "capped": False,
                   "count": 0}
        self.started = time.time()
        stopped = False
        for idx, it in enumerate(self.spec.items):
            item_id = it["id"]
            if stopped or idx >= self.max_iterations or \
                    time.time() - self.started > self.max_duration:
                self._set_status(item_id, ST_CAPPED)
                summary["items"].append({"item_id": item_id,
                                         "status": ST_CAPPED})
                if not stopped and (idx >= self.max_iterations or
                                    time.time() - self.started >
                                    self.max_duration):
                    summary["capped"] = True
                    stopped = True
                continue
            self.plan_item(item_id)
            allowed = bool(op(item_id))
            self.decide(item_id, allowed, operator="operator")
            apply = self.apply_item(item_id)
            summary["items"].append({"item_id": item_id,
                                     "status": apply.get("status")})
        summary["count"] = len(summary["items"])
        return summary

    def resume(self):
        """Reconcile (6C) then continue unstarted/denied items.

        Completed and rolled-back items are never re-executed. Deterministic,
        idempotent restart hook: it only re-plans (dry-run) items that are not
        already terminal.
        """
        try:
            self.coordinator.reconcile()
        except Exception:
            pass
        summary = {"spec_id": self.spec.id, "items": [],
                   "reconciled": True}
        for it in self.spec.items:
            item_id = it["id"]
            st = self.item_status(item_id)
            if st in (ST_COMPLETED, ST_ROLLED_BACK, ST_CAPPED):
                summary["items"].append({"item_id": item_id, "status": st})
                continue
            self.plan_item(item_id)
            summary["items"].append({"item_id": item_id,
                                     "status": self.item_status(item_id)})
        return summary

    def summary(self):
        return [{"item_id": it["id"], "status": self.item_status(it["id"]),
                 "decision": self.item_decision(it["id"])}
                for it in self.spec.items]
