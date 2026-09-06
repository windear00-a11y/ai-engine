# Slice 6C — Crash recovery / restart reconciliation — implementation plan

Slice-format plan per `docs/MASTER-IMPLEMENTATION-PLAN.md` Sections 7, 11, 12.

## 1. Purpose
On (re)start, deterministically reconcile persisted tasks/steps so an
interrupted coordinator run never leaves a task pinned in `running` or a step
pinned in `pending`/`running` forever. Marks crashed in-flight work as
`failed`/`skipped` with reason `interrupted_restart`. NEVER auto-retries.

## 2. Current state
- 6A persists `tasks`/`task_steps` with guarded transitions
  (`TASK_TRANSITIONS`, `TASK_STEP_TRANSITIONS`).
- 6B (`engine/coordinator.py`) claims tasks (`planned -> running`) and drives
  per-step `pending -> running -> success/failed`, then terminal task status.
- **Gap:** if a process dies mid-run, the task row stays `running` and any
  write-ahead step stays `pending`, or an executing step stays `running`.
  Nothing detects or reconciles those orphans today.

## 3. Gap being closed
A restart hook that scans the state DB and, for every task still `running`,
marks remaining `pending`/`running` steps `skipped` (reason
`interrupted_restart`) and marks the task `failed` (same reason). Idempotent:
a second run touches nothing. Unclaimed `planned` tasks are left untouched.

## 4. Architecture
New module `engine/recovery.py` with a single deterministic entry point
`reconcile(state)`, plus a thin `Coordinator.reconcile()` convenience in
`engine/coordinator.py` delegating to it. The coordinator already holds the
`EngineState`; recovery reads+marks ONLY through the existing 6A methods so no
schema or transition escape hatch is introduced.

`reconcile(state)` logic:

1. Find all tasks with `status == 'running'`.
2. For each such task, for each step in `list_task_steps`:
   - still `pending` -> legal advance `pending -> running`, then
     `running -> skipped` (both edges exist in `TASK_STEP_TRANSITIONS`);
     record `error='interrupted_restart'` via `update_step_result`.
   - still `running` -> `running -> skipped`; record error as above.
   - already `success`/`failed`/`planned`/`skipped` -> untouched.
3. Mark the task `running -> failed` with
   `status_reason='interrupted_restart'` (edge exists in `TASK_TRANSITIONS`).
4. Return a deterministic report `{reconciled: [task_id, ...], count: n}`.

Failure handling: every transition uses `expected_status=` compare-and-set;
any unexpected row state fails closed and is reported, never silently
overwritten. `reconcile` never opens the production `knowledge.db`.

## 5. Dependencies
6A transitions + 6B coordinator (its write-ahead `pending`/`running` step
intents are exactly what recovery observes).

## 6. Scope / files
- **New:** `engine/recovery.py` (`reconcile`).
- **Edit:** `engine/coordinator.py` — add `reconcile()` delegating to
  `engine.recovery.reconcile(self.state)` (no behavior change to `run`).
- **New:** `tests/test_recovery.py`.
- **No changes** to `tools/permissions/*`, `api/*`, `database/*`.

## 7. Tests (test matrix)
1. Fresh/empty temp state DB -> `reconcile` returns `{reconciled: [], count: 0}`.
2. Simulated crash: create planned task, claim, one step already `success`,
   another `running`, a later write-ahead step `pending` -> reconcile ->
   task `failed` (reason `interrupted_restart`), `success` step untouched,
   `running`+`pending` steps `skipped` with that reason; steps terminal.
3. Idempotency: reconcile twice -> second result `count: 0`, state identical.
4. Unclaimed `planned` task + a `completed` task are left untouched.
5. Coordinator integration: full 6B `submit`+`run` to completion then
   reconcile -> `count: 0` (nothing to reconcile).
6. Coordinator integration: `submit` only (never run) -> reconcile -> `count: 0`
   (planned task is not in-flight).
7. Invariant: production `knowledge.db` SHA unchanged; temp dirs only.
8. Transition legality: no raw SQL anywhere in recovery paths (assert via
   import surface check in test: `engine.recovery` only calls EngineState
   methods, not `sqlite3`).

## 8. Security checklist
- Marks state only for the exact owner's in-flight rows (task status `running`);
  no auto-retry, no re-execution.
- Reads+writes through 6A methods only; no schema/DDL.
- No network, no git, no filesystem outside the state DB.
- Rooted in the caller's `EngineState`; production KB untouched.

## 9. Persistence
Only `tasks`/`task_steps` rows transitioned per the 6A tables; the
`interrupted_restart` reason is persisted in `status_reason`/step `error`.

## 10. Failure / recovery
This slice IS the recovery. A crash mid-reconcile simply runs again next start
(idempotent by construction).

## 11. Audit gates
- Idempotency (reconcile twice == once).
- No illegal transitions (all via `expected_status` guarded updates).
- Invariants: `knowledge.db` SHA + `CONTRACT_VERSION` untouched.

## 12. Commit checkpoints
- `6C crash recovery and restart reconciliation` (after GO audit + explicit
  operator commit instruction).

## 13. Non-goals
No automatic resumption of interrupted work (operator decides); no task-level
rollback (6D); no ownership hardening (6E); no list API (6F); no git tooling
(Layer 7); no automation (Layer 8).

## 14. Risks
- Over-credulous recovery (marking healthy work failed) — prevented by
  touching ONLY `running` tasks with unclaimed remainder; tests 2,4,5,6 gate it.
- Step already persisted as `pending` but belonging to a `planned` (unclaimed)
  task is never touched (task not running).

## 15. Definition of Done
- All test items 1-8 green.
- 6A + 6B + this suite green; regressions green.
- Reconcile-twice == once proven; production KB SHA unchanged.
- Approved for implementation, then for commit, as a human gate.