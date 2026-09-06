# Slice 6E — Ownership hardening of the persisted task layer — implementation plan

Slice-format plan per `docs/MASTER-IMPLEMENTATION-PLAN.md` Sections 7, 11, 12.

## 1. Purpose
Make single-owner integrity a property of the PERSISTED layer, not just of
coordinator call sites. An `owner_token` alone must (a) grant that owner full
read/write/rollback access to their tasks and (b) deny every other caller on
every EngineState data path — with fail-closed semantics on the boundary.

## 2. Current state
- `claim_task` atomically assigns `owner_token` (planned -> running).
- `PersistentCoordinator._assert_owner` guards `run` and `rollback`.
- **Gap:** EngineState itself is owner-blind. `get_task`, `list_task_steps`,
  `attach_operation`, `update_task_step_status`, `update_step_result`,
  `update_task_result` are callable directly with no ownership check. Any code
  that holds a state handle can read or mutate any task.

## 3. Gap being closed
Ownership enforced AT THE DATA SURFACE. After 6E, direct unguarded EngineState
access to task/step data does not exist in production call paths; every
persisted read/write binds to the row owner and fails closed for everyone
else, including the owner-less coordinator default.

## 4. Architecture
New module `engine/ownership.py` — `OwnerScope` (a deny-by-default guard):

- `OwnerScope(state, owner_token)` exposes ONLY the owner-scoped slice of
  EngineState:
    - reads: `get_task(task_id)`, `list_task_steps(task_id)`,
      `get_task_step(task_id, step_id)`, `get_task_result(task_id)`.
    - writes: `attach_operation`, `update_task_step_status`,
      `update_step_result`, `update_task_status`, `update_task_result`,
      `ensure_task_step` (append-only intent by the same owner).
    - rollback/recovery composition still lives on the coordinator.
- Every method: resolve the task row (or step's task) first, compare
  `owner_token`; mismatch -> `{"ok": False, "error": "not owner", "denied":
  True}` with no read/write side effects (fail closed). Missing row -> denied.
- `ensure_task_step` requires the owning task row to exist (already FK) AND
  the caller token to match it.
- No schema change, no new columns: ownership already lives on the task row;
  6E only guards the surface.
- `OwnerScope.state` is `None`-safe (no state -> everything denied).

Coordinator wiring: `PersistentCoordinator` obtains `self.scope =
OwnerScope(self.state, self.owner_token)` and routes its per-step reads/writes
through `self.scope`. `engine.recovery` scans are system-level (no owner) and
remain on raw `EngineState` as today; `engine.rollback.TaskRollback` receives
the owner-scoped writes (owner gate still applies via coordinator.rollback).

## 5. Dependencies
6A persistence (row owner_token, guarded task/step transitions), 6B
coordinator call sites, 6C recovery scan, 6D rollback hook.

## 6. Scope / files
- **New:** `engine/ownership.py` (`OwnerScope`).
- **Edit:** `engine/coordinator.py` — bind per-step execution reads/writes to
  `self.scope` (behavior unchanged, all existing tests must still pass).
- **Edit (minimal):** `engine/rollback.py` — accept an optional owner-scoped
  reader for evidence reads; default keeps current behavior when None.
- **New:** `tests/test_ownership.py`.
- **No changes** to `docs/CODING-TOOL-ROADMAP.md`, `api/*`, `database/*`.
  `engine/recovery.py` untouched (system-level scan).

## 7. Tests (test matrix)
1. `OwnerScope.get_task` for another owner returns denied (no data leak).
2. `list_task_steps` / `get_task_step` cross-owner -> denied.
3. `attach_operation`, `update_task_step_status`, `update_step_result`,
   `update_task_status`, `update_task_result` cross-owner -> denied + row
   unchanged (transition guard equivalence: bytes identical after).
4. Same-owner calls succeed exactly like raw EngineState today (parity).
5. `ensure_task_step` on another owner's task -> denied (no side effect).
6. Missing task -> denied (not "not found" leakage beyond a boolean).
7. Coordinator full lifecycle (run + rollback) still passes under OwnerScope
   routing; cross-owner claim still fails (regression via existing suite).
8. Invariant: production `knowledge.db` SHA unchanged.
9. Deny identity: `OwnerScope` never opens `knowledge.db`; reads/writes go
   only through the wrapped EngineState.

## 8. Security checklist
- Fail closed on the boundary for EVERY method (no partial exposure).
- No owner coercion: a caller cannot change a task's owner (no such method).
- Recovery/tools/permissions/rollback behavior unchanged for owners.
- No new persistence schema; nothing written for a denied call.

## 9. Persistence
Same schema; only behavioral enforcement on reads + write guards.

## 10. Failure / recovery
A denied call is a programming error upstream → surfaces as `ok: False,
denied: True`; the coordinator treats it as a fail-closed step error, which
6C/6D already handle.

## 11. Audit gates
- Cross-owner matrix: every method denied + zero side effects (bytes).
- Same-owner parity with raw EngineState on the same fixture.
- Invariants: `knowledge.db` SHA + `CONTRACT_VERSION` untouched.
- No owner-mutating surface introduced.

## 12. Commit checkpoints
- `6E ownership hardening of the persisted task layer` (after GO audit +
  operator commit).

## 13. Non-goals
No multi-user sessions, no token rotation, no RBAC roles (single shared
engine identity per run), no encryption. Coordinator/ownership still a
single-process model.

## 14. Risks
- Widening the guard to reads risks breaking `engine.recovery` scan:
  mitigation = recovery keeps using raw EngineState system-level scan
  (documented, unchanged).
- Parity drift between OwnerScope and EngineState: mitigation = same-fixture
  parity tests (items 4/7).

## 15. Definition of Done
- Full cross-owner matrix (items 1-3,6) red before / green after.
- Coordinator suite (6B/6C/6D + ownership 9) green; regressions green.
- Production KB SHA unchanged.