# Slice 6D — Task-level rollback aggregation — implementation plan

Slice-format plan per `docs/MASTER-IMPLEMENTATION-PLAN.md` Sections 7, 11, 12.

## 1. Purpose
When a required step of a persisted task fails, roll back EVERY journaled
operation belonging to that task — aggregated, deterministic, in reverse
journal order — and mark the task `rolled_back` with rollback evidence.

## 2. Current state
- Single-operation rollback exists: `RollbackExecutor` (`tools/permissions/
  rollback.py`) + `ApprovalGateway.authorize_rollback` (confirm/override) +
  task_engine `rollback.operation` / `rollback.confirm` tools.
- 6A persists `tasks`/`task_steps` with a `task_steps.operation_id` FK link
  (`attach_operation`, 6A), the exact hook 6D needs.
- 6B coordinator runs tasks durably; 6C reconciles crashed runs.
- **Gap:** rollback is per single operation. Nothing aggregates all ops of a
  task, orders them deterministically, or sets task status `rolled_back`.

## 3. Gap being closed
A task-level rollback that: (1) collects every operation_id attached to the
task's steps, (2) rolls them back in reverse journal order via the existing
RollbackExecutor path, (3) records aggregate evidence, (4) moves the task from
`running` to `rolled_back` (a legal `TASK_TRANSITIONS` edge: running->rolled_back).

## 4. Architecture
New module `engine/rollback.py` (name clash-free; locals import as
`engine.rollback`) — `TaskRollback`:

- `plan(task_id)` — aggregate, read-only:
  walk `list_task_steps(task_id)`, collect non-null `operation_id` in
  step order, then reverse for rollback order. Reuse `RollbackExecutor.plan`
  per operation to decide safety; compose a task-level manifest.
- `execute(task_id)` — approval-gated:
  for each op in reverse order, go through the SAME gate path the coordinator
  uses (ApprovalGate.authorize_rollback via a forged step-level operation
  record owned by the task), apply `RollbackExecutor.execute`; on each rollback
  append the result to an evidence list; stop-on-first-error (fail closed) or
  continue per configured policy (default stop-on-error, matching task_engine).
- Finalize: `attach_operation`-style evidence recorded in
  `update_task_result(task_id, rollback_manifest_json, planner_version=...)`
  then `update_task_status(task_id, 'rolled_back',
  expected_status='running', status_reason='rollback_aggregated')`.
- Never re-executes forward work; never auto-retries; never opens
  `knowledge.db`. Coordinator hook: `PersistentCoordinator.rollback(task_id)`
  delegating to `engine.rollback.TaskRollback(self.state, ...)`.

The coordinator flow becomes:
`failed step -> (operator decision or configured policy) -> TaskRollback ->
rolled_back (terminal)`.

## 5. Dependencies
6A (`task_steps.operation_id`, `list_task_steps`, guarded statuses), 6B
(coordinator wiring + owner), RollbackExecutor + ApprovalGate from Layer 5,
journal ordering (append order in `journal` table = rollback order).

## 6. Scope / files
- **New:** `engine/rollback.py` (`TaskRollback.plan`, `.execute`).
- **Edit:** `engine/coordinator.py` — add `rollback()` hook (no behavior
  change to `run`).
- **New:** `tests/test_task_rollback.py`.
- **No changes** to `tools/permissions/*`, `api/*`, `database/*`.

## 7. Tests (test matrix)
1. Empty plan for a task with no operations -> `plan` returns
   `{operations: [], safe: True}`; `execute` is a no-op that still marks
   `rolled_back` only when task allows it (or fails closed without ops).
2. Multi-step write task (2-3 approved writes via coordinator) -> `plan`
   returns all operation_ids in reverse step order.
3. Full cycle: run a task to a failing step with prior approved writes ->
   `rollback` -> every file restored to before-image; task status
   `rolled_back`; evidence list length == write count; order reversed.
4. Determinism: two `plan` calls return identical manifests (same ids/order).
5. Idempotency: second `execute` on an already-terminal task fails closed
   (no double rollback, no mutation).
6. Unapproved writes are not in scope (denied writes never attached).
7. Wrong-owner rollback attempt fails closed.
8. Invariant: production `knowledge.db` SHA unchanged; temp dirs only.

## 8. Security checklist
- Rollback is itself an approval-gated, journaled action (no silent undo).
- Only operations owned by the task (via `attach_operation` link) are rolled
  back.
- Deny-by-default: no approver => no rollback granted.
- No schema/DDL; only 6A read/mark methods + Layer 5 RollbackExecutor.

## 9. Persistence
Rollback evidence appended to operations/audit; task marked `rolled_back`
(terminal). Nothing else is written.

## 10. Failure / recovery
Partial rollback impossible by design: journal replay is deterministic;
failure mid-replay leaves remaining ops listed as NOT rolled back in the
evidence and the task stays non-terminal for 6C-style operator attention.

## 11. Audit gates
- Ordering determinism + snapshot-restore verification (every restored path
  checksum-matched after rollback).
- Status legality (only running->rolled_back).
- Invariants: `knowledge.db` SHA + `CONTRACT_VERSION` untouched.

## 12. Commit checkpoints
- `6D task-level rollback aggregation` (after GO audit + operator commit).

## 13. Non-goals
No compensating new writes (undo only); no rollback policy automation beyond a
single stop/continue flag; no crash recovery (6C owns that), no ownership
hardening (6E), no git tooling (Layer 7).

## 14. Risks
- Content drift between snapshot and rollback time: RollbackExecutor already
  detects changed files → `rollback_conflict` (handled per-op).
- Complexity of reverse order: enforced by construction (reverse of
  deterministic forward order).

## 15. Definition of Done
- All test items 1-8 green; regressions green (Layer 5 rollback + 6A/B/C).
- Restore-on-failure proven for each tool kind exercised.
- Production KB SHA unchanged.