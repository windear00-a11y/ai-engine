# Slice 6B — Persistent Execution Coordinator — implementation plan

Slice-format plan per `docs/MASTER-IMPLEMENTATION-PLAN.md` Sections 7, 11, 12.
This is the deliverable of the `PREPARE_PLAN` step; it authorizes **planning
only**. Implementation starts only after operator approval of this plan.

## 1. Purpose
A durable coordinator that converts a planned task into a claimed task with
per-step execution, persistence, and journal attachment — one task identity
carried from planner output to final status with every mutation journaled.

## 2. Current state
- Planner (`tools/planner/deterministic.py`) emits plans as TaskEngine tasks.
- `TaskEngine` (`engine/task_engine.py:42`) runs tasks fully in memory; its
  `run_task` (line 356) drives steps and returns in-memory records.
- `EngineState` (6A) persists `tasks` + `task_steps` (guarded transitions
  `TASK_TRANSITIONS`/`TASK_STEP_TRANSITIONS`, claim-atomically via
  `claim_task`). `attach_operation` links a journal operation to a step.
- `ApprovalGate` (deny-by-default) + `ExecutionRunner`/`WriteRunner` +
  `RollbackExecutor` exist; TaskEngine already accepts a wired gate
  (`task_engine.py:51-77`).
- **Gap:** there is NO durable production line from planner output to
  post-claim execution to journal; nothing persists a run or reuses the 6A
  task tables outside tests.

## 3. Gap being closed
Connect planner -> `create_task` -> `claim` -> per-step persistence ->
approved tool execution -> `attach_operation` -> `update_step_result` ->
terminal task status, under one task identity.

## 4. Architecture (recommended; resolves the OPEN DECISION at §15-6B-4)
New module `engine/coordinator.py` — a **PersistentCoordinator** that drives
`TaskEngine` as the pure execution driver. It does NOT modify
`engine/task_engine.py`'s public behavior.

- Constructor:
  `PersistentCoordinator(state: EngineState, engine: TaskEngine,
  owner_token: str, approver=None)`.
- `submit(planner_output) -> task` — validate via `TaskEngine.validate_task`,
  then `state.create_task(id, task_json, workspace_root, status="planned")`.
- `run(task_id) -> report` —
  1. `state.claim_task(task_id, owner_token)`; reject if not claimed.
  2. Permission setup: `engine.permissions` must be an `ApprovalGate`; the
     run asserts owner on every transition (MEDIUM-2 is enforced here for
     6B-created tasks only; full hardening remains 6E).
  3. For each step in plan order:
     - `state.ensure_task_step(task_id, step_id, idx, tool, inputs_json,
       status="pending")`.
     - `state.update_task_step_status(... "pending" -> "running",
       expected_status="pending")`.
     - Resolve `$ref` inputs via `TaskEngine._resolve_inputs`.
     - Execute exactly one registered tool through the existing gate +
       runner (mutation path = approval-gated `WriteRunner`, sandboxed
       `project.build/test` via `ExecutionRunner`). Dry-run keeps mutating
       tools as `planned` (no execution, no journal op).
     - Capture result/error + duration exactly as `TaskEngine._run_step`
       does (task_engine.py:301-352).
     - If the step produced a journal operation, call
       `state.attach_operation(task_id, step_id, operation_id)`.
     - `state.update_task_step_status(... "running" -> "success"|"failed")`
       and `state.update_step_result(...)`.
  4. `state.update_task_status(task_id, "running" -> "completed"|"failed"|"invalid")`
     with `finished_at_epoch`; write `result_json` via `update_task_result`.
- The coordinator's observable per-step and overall statuses mirror
  `TaskEngine.run_task` byte-for-byte for the non-persisted equivalent task.
- `submit` terminally rejects unknown tools and invalid transition attempts
  (all fail closed, matching 6A).

## 5. Dependencies
6A tables + guarded transitions; planner; `engine/task_engine.py` (registry,
`_resolve_inputs`, `MUTATING`); `ApprovalGate`/`PathPolicy`/`Policy`;
`ExecutionRunner`/`WriteRunner`; `RollbackExecutor`; journal `attach_operation`.

## 6. Scope / files
- **New:** `engine/coordinator.py` (PersistentCoordinator, `submit`, `run`,
  `_execute_step`, `_finalize`).
- **New:** `tests/test_coordinator.py`.
- **No changes** to `engine/task_engine.py`, `tools/permissions/*`,
  `api/*`, `database/*`, or any knowledge engine file in this slice.

## 7. Tests (test matrix)
1. Unit: `submit` -> planned task row; idempotent re-submit for identical
   data.
2. Unit: `claim` ownership — second claim of same task fails; owner_token
   required.
3. Unit: full lifecycle plan->planned->running->completed via temp EngineState
   (explicit `db_path=`), read-knowledge steps only; per-step rows
   pending->running->success.
4. Unit: `$ref` passing between persisted steps (knowledge + coding chain).
   Result equality with `TaskEngine.run_task` on the same task (byte-identical
   `status`/step statuses).
5. Unit: failing step (tool error) -> step `failed`, task `failed`,
   `finished_at_epoch` set; `stop_on_error=False` continues.
6. Unit: dry-run task -> mutating tools stay `planned`, no journal operation
   and no file change in the temp workspace.
7. Unit: write step via approved `WriteRunner` -> journal operation attached
   (`attach_operation`), rollback-capable; unapproved write -> step `failed`,
   nothing journaled.
8. Unit: wrong-owner post-claim transition rejected (fail closed).
9. Invariant: coordinator tests never open the production `knowledge.db`
   (temp dirs only); knowledge.db SHA assertion via the 6A pattern.
10. Regression hook: `test_engine_state_tasks` (6A) and 6B suite must both
    stay green.

## 8. Security checklist
- Only registered `TaskEngine` tools; unknown tool -> submission rejected.
- Deny-by-default: no approver wired => no write approval ever granted.
- Owner token asserted on every post-claim transition (6B tasks).
- All writes via `WriteRunner` (snapshot + journaled); build/test sandboxed.
- Secrets stripped by existing `Policy` paths; results JSON-canonicalized.
- Temp dirs only in tests; production DBs never opened.

## 9. Persistence
Every task/step status lives in the configured state DB (`tasks`,
`task_steps`); every approved mutation is recoverable via the journal and the
step-level `attach_operation` link.

## 10. Failure / recovery
A crash mid-step leaves that step `running` (`pending` if not yet claimed)
with the journal as source of truth; restart reconciliation is explicitly 6C.
Practically: `ensure_task_step` is write-ahead, so the intent survives.

## 11. Audit gates (this slice)
- Schema/transition legality (6A tables untouched).
- Ownership enforcement on every step + task transition.
- Determinism: two identical submissions produce identical states.
- Invariants: `knowledge.db` SHA + `CONTRACT_VERSION` untouched.
- Git state: only `engine/coordinator.py` + `tests/test_coordinator.py`.

## 12. Commit checkpoints
- `6B persistent execution coordinator above TaskEngine` (after GO audit +
  explicit operator commit instruction).

## 13. Non-goals
No crash recovery (6C), no task-level rollback aggregation (6D), no MEDIUM-1
lock-strategy redesign (6E), no read-only list API (6F), no git tooling
(Layer 7), no automation (Layer 8).

## 14. Risks
- MEDIUM-1 (busy_timeout storm) must not regress: coordinator keeps single
  writer per task, tests keep the 5x concurrency checks from 6A green.
- Persisted behavior must stay byte-identical to in-memory `TaskEngine`.
- New coordinator must not become an autonomous scheduler (no auto-run).

## 15. Definition of Done
- `submit` + `run` lifecycle green incl. ownership test (#2, #8).
- Planner-output integration test green (#4).
- Byte-identical comparison with `TaskEngine.run_task` green (#4).
- No production DB touched; regressions (6A 34 + 6B + prior layers) green.
- Approved for implementation, then for commit, as a human gate.