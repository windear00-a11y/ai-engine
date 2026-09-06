# Slice 6F — Public task listing/query API — implementation plan

Slice-format plan per `docs/MASTER-IMPLEMENTATION-PLAN.md` Sections 7, 11, 12.

## 1. Purpose
Expose a PUBLIC, read-only, system-level query surface over the persisted
task layer so callers never need private `EngineState` internals. This retires
the documented 6C LOW finding (raw `state._connect()` scan in recovery) and
gives the workflow controller a sanctioned way to enumerate tasks.

## 2. Current state
- Engines's persistence (6A) exposes per-task reads (`get_task`,
  `get_task_step`, `list_task_steps`) but NO way to enumerate tasks.
- 6C recovery needed "list running task ids" and used
  `state._connect()` + a hand-written `SELECT ... WHERE status = 'running'`
  as a private workaround, flagged LOW ("interim read-only scan pending a 6F
  list API").
- 6E hardened per-task access; the system-level scan intentionally stayed raw.

## 3. Gap being closed
A public `EngineState.list_tasks(...)` used by recovery (and any controller)
so the private connection + SQL lives nowhere in production call paths.

## 4. Architecture
Extend `tools/permissions/journal.py` (the layer-6 persistence surface, owned
by this layer) with ONE read-only method:

- `EngineState.list_tasks(status=None)` -> list of task summaries (task_id,
  status, status_reason, owner_token, created/updated/finished epochs,
  planner_version), deterministic (task_id ASC). `status=None` lists all;
  `status='running'` filters. Pure read: no `status` filtering on the client,
  no mutation, no writes.
- Validation: unknown status string -> fail closed (empty list + no query).
- Deny-by-default stays at the OwnerScope layer (6E); `list_tasks` is
  SYSTEM-level (used by recovery across owners, like the 6C scan).

`engine/recovery.py`: replace the `_connect()`/raw-SELECT scan with
`self.state.list_tasks(status='running')`. Same behaviour, no private API.
- `reconcile()` result identical; tests unchanged in behaviour.

`engine/coordinator.py`: no change (reconcile already delegates).

## 5. Dependencies
6A persistence schema + `_connect` pattern; 6C recovery scan semantics;
6E ownership guard (which re-exposes per-task reads, not the system scan).

## 6. Scope / files
- **Edit:** `tools/permissions/journal.py` — add `list_tasks` (read-only).
- **Edit:** `engine/recovery.py` — scan via `list_tasks(status='running')`.
- **Edit:** `tests/test_engine_state_tasks.py` — add list_tasks tests.
- **Edit:** `tests/test_recovery.py` — source-surface test now expects
  `list_tasks` and rejects the private `_connect` in recovery.
- **No changes** to engine/coordinator.py, engine/rollback.py,
  engine/ownership.py, `docs/CODING-TOOL-ROADMAP.md`, `api/*`, `database/*`.

## 7. Tests (test matrix)
1. Empty state -> `list_tasks()` == [] ; `list_tasks('running')` == [].
2. After N mixed-status created tasks: `list_tasks()` returns all, ordered by
   task_id; `list_tasks('running')` returns only running; unknown status
   string -> [] without querying.
3. Summary fields present and match rows (status/owner/planner_version).
4. Read-only proof: `list_tasks` never mutates (timestamps/bytes identical
   before/after on a populated store).
5. Recovery parity: reconcile() results byte-identical before/after the scan
   change (uses the seeded crash fixtures from 6C).
6. Recovery surface test: engine/recovery source contains `list_tasks` and NO
   `_connect`/raw SELECT; `list_tasks` itself is present in journal.py.
7. Invariant: production `knowledge.db` SHA unchanged.

## 8. Security checklist
- Read-only method; no status mutation surface.
- Deterministic ordering; no unbounded query (no limit — dataset is bounded
  to tasks, a small table; ORDER otherwise stable).
- No new owner-binding on the system scan (owner scoping remains 6E's job on
  the per-task surface).
- No schema/DDL; SELECT-only.

## 9. Persistence
No schema change; read-only SELECT.

## 10. Failure / recovery
list_tasks failing (e.g. DB unavailable) surfaces an error via the standard
EngineState connection-failure path; recovery treats it as a fail-closed
reconcile (no crash, no partial marking).

## 11. Audit gates
- Recovery no longer contains private `_connect`/SQL (retires 6C LOW).
- Determinism + filter correctness tests green.
- Invariants: `knowledge.db` SHA + `CONTRACT_VERSION` untouched.
- `tests/test_recovery.py` and `tests/test_engine_state_tasks.py` updated in
  lockstep (no stale assertions).

## 12. Commit checkpoints
- `6F public task listing/query API` (after GO audit + operator commit).

## 13. Non-goals
No pagination/limit, no joins, no aggregation, no arbitrary SQL, no write
enumerations, no RBAC on the system scan (single shared identity).

## 14. Risks
- Regression in recovery if filter semantics drift: mitigated by parity
  fixture (item 5) reusing the 6C crash shapes.
- Private-API usage reappearing in recovery: mitigated by the source-surface
  test (item 6).

## 15. Definition of Done
- list_tasks tests (items 1-4) green; recovery parity (item 5) green;
  surface test (item 6) green; full suites green.
- Production KB SHA unchanged.