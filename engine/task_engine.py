"""Task Engine / Workflow Layer.

The Task Engine is the deterministic execution coordinator. It is NOT an AI
brain: it combines the existing Knowledge Tools and Coding Tools into bounded,
multi-step workflows and controls *how* approved actions happen. A future
planner/AI decides *what* should happen; this engine executes it.

Design
------
* A task is a declarative object: ``{id, description, steps, stop_on_error,
  max_steps, max_duration, dry_run}``.
* A step is ``{id, tool, inputs}``. ``tool`` must be one of a fixed registry of
  approved tool names; arbitrary names are rejected.
* A step input value may contain a reference ``{"$ref": "stepId.path[0].key"}``
  pointing at an earlier step's stored result. This is the *only* cross-step
  data mechanism — no expression language, no recursion, no loops.
* Steps run in order. Each step validates its inputs, executes exactly one
  approved tool, captures the structured result, and records success/failure.
* ``dry_run`` executes read-only tools but turns mutating tools
  (``file.write/edit/mkdir``, ``project.build/test``) into planned actions.
* Hard limits: ``max_steps`` (validated up front), ``max_duration`` (wall-clock
  guard that skips remaining steps), no recursive task spawning, no loops.
* Every step emits an audit record that is JSON-serializable.

No AI/LLM, no network, no unrestricted shell, no package installation.
"""

import os
import time

from tools.knowledge_tools import KnowledgeTools
from tools.coding import CodingTools

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_KNOWLEDGE_DIR = os.path.join(_ROOT, "knowledge")
DEFAULT_WORKSPACE = os.path.join(_ROOT, "workspace")

DEFAULT_MAX_STEPS = 100
DEFAULT_MAX_DURATION = 300


class TaskEngine:
    # Tools that change state / run builds or tests. In dry-run these are
    # represented as planned actions rather than executed.
    MUTATING = {
        "file.write", "file.edit", "file.mkdir",
        "project.build", "project.test",
        "rollback.operation", "rollback.confirm",
        "git.stage", "git.commit",
    }

    def __init__(self, knowledge_dir=None, workspace_root=None,
                 max_steps=DEFAULT_MAX_STEPS, max_duration=DEFAULT_MAX_DURATION,
                 permissions=None, policy=None, approver=None):
        # Fail-safe default: the production coding-tool path must never be
        # reachable ungated. If the caller omits the optional permission
        # object, construct a real gate that denies every non-read operation
        # unless an explicit approver is supplied (no auto-approval). A caller
        # who forgets to wire an approver gets denials, never a bypass.
        workspace_root = workspace_root or DEFAULT_WORKSPACE
        if permissions is None:
            from tools.permissions import Policy, PathPolicy, ApprovalGate
            if policy is None:
                policy = Policy()
            # Fail-closed gate with NO persistent state: it denies every
            # non-read operation by default, so no journal is ever produced
            # and no rollback is ever required. Callers that want journaled
            # writes + rollback supply an ApprovalGate bound to an EngineState
            # (the production coordinator does this).
            permissions = ApprovalGate(
                path_policy=PathPolicy(workspace_root, policy=policy),
                approver=approver)
        self.knowledge = KnowledgeTools(
            knowledge_dir=knowledge_dir or DEFAULT_KNOWLEDGE_DIR)
        self.coding = CodingTools(
            workspace_root,
            permissions=permissions, policy=policy, approver=approver)
        self.permissions = permissions
        self.default_max_steps = max_steps
        self.default_max_duration = max_duration
        self._current_task_id = None
        from tools.git import GitTools
        self.git = GitTools(workspace_root, permissions=permissions)
        self.registry = self._build_registry()

    def _build_registry(self):
        k, c = self.knowledge, self.coding
        return {
            "knowledge.search": k.search,
            "knowledge.get": k.get,
            "knowledge.related": k.related,
            "knowledge.follow": k.follow,
            "file.list": c.file_list,
            "file.read": c.file_read,
            "file.search": c.file_search,
            "project.inspect": c.project_inspect,
            "code.analyze": c.code_analyze,
            "file.write": c.file_write,
            "file.edit": c.file_edit,
            "file.mkdir": c.file_mkdir,
            "file.diff": c.file_diff,
            "project.check": c.project_check,
            "project.build": c.project_build,
            "project.test": c.project_test,
            "rollback.operation": self._rollback_operation,
            "rollback.confirm": self._rollback_confirm,
            "planner.generate": self._planner_generate,
            "git.status": self.git.status,
            "git.diff": self.git.diff,
            "git.log": self.git.log,
            "git.stage": self.git.stage,
            "git.commit": self.git.commit,
        }

    def _planner_generate(self, intent=None, target=None, error=None,
                          constraints=None, workspace_root=None, db_path=None):
        """Non-mutating planner invocation (Component C).

        Produces a TaskEngine-compatible task via the deterministic rule/template
        planner. Does not write, execute, or approve anything.
        """
        ws = workspace_root or getattr(self.coding, "root", None) or DEFAULT_WORKSPACE
        db = db_path or os.path.join(os.path.realpath(ws), ".ai-engine", "project_index.db")
        from tools.planner.deterministic import DeterministicPlanner
        planner = DeterministicPlanner(ws, db_path=db)
        return planner.generate(intent=intent, target=target, error=error,
                                constraints=constraints)

    # -- rollback tools ----------------------------------------------------

    def _rollback_executor(self):
        from tools.permissions.rollback import RollbackExecutor
        pp = getattr(self.permissions, "path_policy", None)
        state = getattr(self.permissions, "state", None)
        if pp is None or state is None:
            return None
        return RollbackExecutor(pp, state)

    def _rollback_operation(self, operation_id):
        """Auto-rollback of a completed write operation (decision 7/8/9).

        Succeeds without extra approval only when every file still matches the
        post-write checksum. Any external modification produces a
        ``rollback_conflict`` instead of silently overwriting.
        """
        if not isinstance(operation_id, str) or not operation_id:
            return {"result": "invalid", "operation_id": operation_id,
                    "error": "operation_id must be a non-empty string"}
        ex = self._rollback_executor()
        if ex is None:
            return {"result": "error", "operation_id": operation_id,
                    "error": "rollback requires a persistent state store"}
        plan = ex.plan(operation_id)
        if plan["not_found"]:
            return {"result": "not_found", "operation_id": operation_id,
                    "error": "unknown operation"}
        if plan["not_owned"]:
            return {"result": "denied", "operation_id": operation_id,
                    "error": plan["summary"]}
        blocked = [r for r in plan["rows"] if r["action"] == "blocked"]
        if blocked:
            return {"result": "denied", "operation_id": operation_id,
                    "error": f"rollback targets are not writable: "
                             f"{blocked[0]['target_rel']}",
                    "blocked": [b["target_rel"] for b in blocked]}
        if not plan["safe"]:
            return {"result": "rollback_conflict",
                    "operation_id": operation_id,
                    "error": "files modified after the write; use "
                             "rollback.confirm to override",
                    "conflicts": plan["conflicts"]}
        allowed, _d, err = self.permissions.authorize_rollback(
            operation_id, ex.resolve_fn, confirm=False)
        if not allowed:
            return {"result": "denied", "operation_id": operation_id,
                    "error": err}
        res = ex.execute(operation_id, confirm=False)
        return {"result": res["result"], "operation_id": operation_id,
                "restored": res.get("restored"), "deleted": res.get("deleted"),
                "conflicts": res.get("conflicts"),
                "notes": res.get("notes"), "error": res.get("error")}

    def _rollback_confirm(self, operation_id):
        """Explicitly confirmed rollback that may override TOCTOU conflicts.

        Still goes through the approval gate and can never restore paths in the
        blocked/read-only zones.
        """
        if not isinstance(operation_id, str) or not operation_id:
            return {"result": "invalid", "operation_id": operation_id,
                    "error": "operation_id must be a non-empty string"}
        ex = self._rollback_executor()
        if ex is None:
            return {"result": "error", "operation_id": operation_id,
                    "error": "rollback requires a persistent state store"}
        plan = ex.plan(operation_id)
        if plan["not_found"]:
            return {"result": "not_found", "operation_id": operation_id,
                    "error": "unknown operation"}
        if plan["not_owned"]:
            return {"result": "denied", "operation_id": operation_id,
                    "error": plan["summary"]}
        blocked = [r for r in plan["rows"] if r["action"] == "blocked"]
        if blocked:
            return {"result": "denied", "operation_id": operation_id,
                    "error": f"rollback targets are not writable: "
                             f"{blocked[0]['target_rel']}",
                    "blocked": [b["target_rel"] for b in blocked]}
        allowed, _d, err = self.permissions.authorize_rollback(
            operation_id, ex.resolve_fn, confirm=True)
        if not allowed:
            return {"result": "denied", "operation_id": operation_id,
                    "error": err}
        res = ex.execute(operation_id, confirm=True)
        return {"result": res["result"], "operation_id": operation_id,
                "restored": res.get("restored"), "deleted": res.get("deleted"),
                "conflicts": res.get("conflicts"),
                "notes": res.get("notes"), "error": res.get("error")}

    # -- validation -------------------------------------------------------

    def validate_task(self, task):
        errors = []
        if not isinstance(task, dict):
            return False, ["task must be an object"]
        if not isinstance(task.get("id"), str) or not task["id"]:
            errors.append("task.id must be a non-empty string")

        steps = task.get("steps")
        if not isinstance(steps, list):
            errors.append("task.steps must be a list")
        else:
            cap = task.get("max_steps", self.default_max_steps)
            if len(steps) > cap:
                errors.append(
                    f"too many steps: {len(steps)} > max_steps {cap}")
            for idx, step in enumerate(steps):
                if not isinstance(step, dict):
                    errors.append(f"step {idx} must be an object")
                    continue
                if not isinstance(step.get("id"), str) or not step["id"]:
                    errors.append(f"step {idx} is missing a valid id")
                if not isinstance(step.get("tool"), str):
                    errors.append(f"step {idx} is missing a tool name")
                elif step["tool"] not in self.registry:
                    errors.append(
                        f"step {idx} uses unknown tool: {step['tool']!r}")
        return (len(errors) == 0, errors)

    # -- reference resolution --------------------------------------------

    @staticmethod
    def _resolve_inputs(inputs, store):
        if isinstance(inputs, dict):
            if set(inputs.keys()) == {"$ref"}:
                return TaskEngine._resolve_ref(inputs["$ref"], store)
            return {k: TaskEngine._resolve_inputs(v, store)
                    for k, v in inputs.items()}
        if isinstance(inputs, list):
            return [TaskEngine._resolve_inputs(v, store) for v in inputs]
        return inputs

    @staticmethod
    def _resolve_ref(ref, store):
        if not isinstance(ref, str) or not ref:
            raise KeyError("invalid $ref")
        parts = ref.split(".")
        node = None
        for i, part in enumerate(parts):
            key, idx = part, None
            if "[" in part:
                key, _, tail = part.partition("[")
                idx = int(tail.rstrip("]"))
            if i == 0:
                if key not in store:
                    raise KeyError(f"unknown step reference: '{key}'")
                node = store[key].get("result")
                if node is None:
                    raise KeyError(f"step '{key}' has no result to reference")
            else:
                if not isinstance(node, dict) or key not in node:
                    raise KeyError(f"cannot resolve '.{part}' in '{ref}'")
                node = node[key]
            if idx is not None:
                if not isinstance(node, list) or idx >= len(node):
                    raise KeyError(f"index {idx} out of range in '{ref}'")
                node = node[idx]
        return node

    # -- step execution ---------------------------------------------------

    def _record(self, step, raw_inputs, resolved_inputs, result, status,
                error, start, dur):
        finished = time.time() if dur is not None else None
        return {
            "task_id": self._current_task_id,
            "id": step["id"],
            "tool": step.get("tool"),
            "raw_inputs": raw_inputs,
            "inputs": resolved_inputs,
            "status": status,
            "result": result,
            "error": error,
            "started_at": start,
            "finished_at": finished,
            "duration": dur,
        }

    def _run_step(self, step, store, dry_run, planned_actions):
        tool_name = step["tool"]
        raw_inputs = step.get("inputs", {}) or {}

        if not isinstance(raw_inputs, dict):
            return self._record(step, raw_inputs, raw_inputs, None,
                                "failed", "inputs must be an object",
                                None, None)

        try:
            resolved = self._resolve_inputs(raw_inputs, store)
        except KeyError as e:
            return self._record(step, raw_inputs, raw_inputs, None,
                                "failed", f"reference error: {e}",
                                None, None)

        fn = self.registry.get(tool_name)
        if fn is None:
            return self._record(step, raw_inputs, resolved, None,
                                "failed", f"unknown tool: {tool_name}",
                                None, None)

        # Dry-run: mutating tools are planned, not executed.
        if dry_run and tool_name in self.MUTATING:
            planned = {
                "dry_run": True, "planned": True, "executed": False,
                "tool": tool_name, "inputs": resolved,
            }
            planned_actions.append({
                "step_id": step["id"], "tool": tool_name, "inputs": resolved,
            })
            return self._record(step, raw_inputs, resolved, planned,
                                "planned", None, None, None)

        start = time.time()
        try:
            result = fn(**resolved)
            if isinstance(result, dict) and result.get("error"):
                return self._record(step, raw_inputs, resolved, result,
                                    "failed", result.get("error"),
                                    start, time.time() - start)
            return self._record(step, raw_inputs, resolved, result,
                                "success", None, start, time.time() - start)
        except TypeError as e:
            return self._record(step, raw_inputs, resolved,
                                {"error": str(e)}, "failed",
                                f"invalid input: {e}", start,
                                time.time() - start)
        except Exception as e:  # defensive: never crash the engine
            return self._record(step, raw_inputs, resolved,
                                {"error": str(e)}, "failed", str(e),
                                start, time.time() - start)

    # -- task execution ---------------------------------------------------

    def run_task(self, task):
        ok, errors = self.validate_task(task)
        if not ok:
            return {
                "task_id": task.get("id") if isinstance(task, dict) else None,
                "status": "invalid",
                "dry_run": bool(task.get("dry_run", False)) if isinstance(task, dict) else False,
                "planned": False,
                "planned_actions": [],
                "step_count": 0,
                "steps": [],
                "audit": [],
                "errors": errors,
                "started_at": None,
                "finished_at": None,
                "duration": None,
            }

        self._current_task_id = task["id"]
        steps = task["steps"]
        dry_run = bool(task.get("dry_run", False))
        stop_on_error = bool(task.get("stop_on_error", True))
        max_duration = task.get("max_duration", self.default_max_duration)
        started = time.time()

        records = []
        store = {}
        errors = []
        planned_actions = []
        overall = "completed"

        for i, step in enumerate(steps):
            # Bounded duration: skip this and all remaining steps.
            if time.time() - started > max_duration:
                for j in range(i, len(steps)):
                    rec = self._record(steps[j], steps[j].get("inputs", {}),
                                       steps[j].get("inputs", {}), None,
                                       "skipped", "duration limit exceeded",
                                       None, None)
                    records.append(rec)
                    errors.append({"step_id": steps[j]["id"],
                                   "error": "duration limit exceeded"})
                overall = "failed" if overall == "completed" else overall
                break

            rec = self._run_step(step, store, dry_run, planned_actions)
            store[step["id"]] = rec
            records.append(rec)

            if rec["status"] == "failed":
                errors.append({"step_id": step["id"], "error": rec["error"]})
                if stop_on_error:
                    overall = "failed"
                    for j in range(i + 1, len(steps)):
                        srec = self._record(
                            steps[j], steps[j].get("inputs", {}),
                            steps[j].get("inputs", {}), None, "skipped",
                            "skipped due to earlier step failure", None, None)
                        records.append(srec)
                        errors.append({"step_id": steps[j]["id"],
                                       "error": "skipped due to earlier step failure"})
                    break
                else:
                    overall = "completed_with_errors"

        if dry_run and overall == "completed":
            overall = "planned"
        elif dry_run and overall == "completed_with_errors":
            overall = "planned_with_errors"

        finished = time.time()
        return {
            "task_id": task["id"],
            "description": task.get("description"),
            "status": overall,
            "dry_run": dry_run,
            "planned": dry_run,
            "planned_actions": planned_actions,
            "step_count": len(records),
            "steps": records,
            "audit": records,
            "errors": errors,
            "started_at": started,
            "finished_at": finished,
            "duration": finished - started,
        }


def run_task(task, knowledge_dir=None, workspace_root=None,
             max_steps=DEFAULT_MAX_STEPS, max_duration=DEFAULT_MAX_DURATION):
    """Convenience: build a :class:`TaskEngine` and run a single task."""
    engine = TaskEngine(knowledge_dir=knowledge_dir,
                        workspace_root=workspace_root,
                        max_steps=max_steps, max_duration=max_duration)
    return engine.run_task(task)
