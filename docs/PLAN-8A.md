# Slice 8A — Deterministic workflow automation over the coordinator — implementation plan

Slice-format plan per `docs/MASTER-IMPLEMENTATION-PLAN.md` §8 and Section 8A.

## 1. Purpose
A bounded, deterministic automation loop (fix proposals -> dry-run -> approval
gate -> apply -> verify -> optionally approval-gated git commit) that drives the
Layer 6 coordinator with the SAME human approval gates as single tasks.
Every loop iteration is a persisted task with steps + audit; denial at any gate
produces zero mutations; iterations are capped and journaled.

## 2. Current state
- 6A/6B: `EngineState` persists tasks/steps/ops; `PersistentCoordinator.submit`
  + `run` drive a task through `TaskEngine` with per-step persistence, dry-run
  (`planned`/`planned_actions`), ownership claims (6E), and stop-on-error.
- 6C: `reconcile` recovers interrupted tasks on restart. 6D: task rollback.
- 7B: approval-gated `git.stage` / `git.commit` via `ApprovalGate.authorize_git`.
- Layer 5 verification: diagnostics parser + `fix_rules.dispatch` produce
  `FixProposal`s; `project.check` / `project.test` tools exist.
- No loop exists: the only automation is single-task, single-pass execution.

## 3. Gap being closed
A `WorkflowEngine` that can run a **sequence of related work items** end-to-end
(e.g. several fix proposals over one workspace) where:
- every work item is a real TaskEngine task persisted through the coordinator;
- each item is FIRST dry-run-planned, then presented to an approver, and only
  runs for real after explicit approval — a denied gate leaves tree + git
  untouched (AND the denial is journaled);
- after apply, a read-only verification phase runs; failure triggers the 6D
  rollback path instead of a broken commit;
- all git writes still require the 7B approval-gated tools;
- iteration count and wall-clock are hard-capped (anti-runaway);
- a restart mid-loop reconciles via 6C and resumes the remaining items.

## 4. Architecture
New module `engine/workflow.py` — a deterministic state machine, no daemon, no
unstructured shell, no concurrency:

- **`WorkflowSpec`** (frozen dataclass): `id`, `workspace_root`, `items`
  (list of planner-valid task dicts), `max_iterations` (default 8, clamp 1..100),
  `max_duration` (wall-clock cap, default e.g. 600s), `policy` (PathPolicy /
  ApprovalGate wiring), `verify_tool` (read-only check to run per item, e.g.
  `project.check`), `commit_on_verify` (bool; uses 7B git tools).
- **`WorkflowEngine(spec, state, engine, coordinator, permissions)`**:
  - `plan_item(item)` -> coordinator dry-run task -> `planned_actions` (no
    mutation). This is the loop's first output.
  - `request_approval(item)` -> records a pending approval (like the roadmap
    controller) with the item id + planned mutation summary; returns a
    decision token. `decide(approve_bool, operator)` audits grant/deny via
    `ApprovalGate.authorize_*` (each mutation tool already gates itself);
    denial -> task stays `planned_denied`, loop skips straight to next item
    AND records the denial in audit.
  - `apply_item(item)` -> coordinator.run (real, mutation allowed) -> cases:
    - success + verify passes -> item `completed`; on `commit_on_verify`,
      `git.stage` the changed files (+ `git.commit`) through the 7B gate.
    - verify fails after mutation -> `coordinator.rollback(item)` (6D) ->
      task `rolled_back`, tree restored; loop records failure and stops
      (stop_on_error) unless spec allows continuing.
  - `run()` -> the bounded loop: iterate items in order; for each: plan ->
    approval -> apply/verify/commit; enforce `max_iterations`
    (`A_CAPPED` when exceeded) and `max_duration`; journal every transition
    into EngineState ops + audit rows.
  - `resume()` -> `coordinator.reconcile()` (6C) then continue unstarted/
    `planned_denied`/`running` items deterministically; never re-executes an
    already-`completed`/`rolled_back` item.
- **Journaling/persistence**: all through 6A/6B EngineState (tasks, steps,
  audit op rows). Audit gate decisions use the same `audit_records()` store as
  the single-task path. Nothing writes the production knowledge DB; no
  `.git/config` mutation; no push.

Dry-run posture: `plan_item` and the verification phase never mutate; mutation
happens only inside `apply_item` after the gate approves the exact planned
actions.

## 5. Dependencies
6B (coordinator), 6A (persistence), 6C (reconcile), 6D (rollback), 7B (gated
commit), 5 (ApprovalGate + verification tools).

## 6. Scope / files
- **New:** `engine/workflow.py` — `WorkflowSpec`, `WorkflowEngine` with
  `plan_item` / `request_approval` / `decide` / `apply_item` / `run` / `resume`.
- **New:** `tests/test_workflow.py` — temp-workspace e2e tests (matrix below).
- **Edit:** none strictly required in `engine/coordinator.py` /
  `engine/task_engine.py`; reuse `PersistentCoordinator.submit/run/rollback/
  reconcile` and the 7B git tools as-is. If a seam is needed (e.g. exposing the
  approval decision token) it stays inside `engine/workflow.py`.
- **No changes** to `docs/CODING-TOOL-ROADMAP.md`, `api/*`, `database/*`,
  `policy.py` defaults, or the git deny-by-default posture.

## 7. Tests (test matrix) — all in a fresh temp workspace + temp git repo
1. **Dry-run ships a plan with no mutations**: `plan_item` returns
   `planned_actions`, every mutating tool is un-executed; workspace tree and
   git HEAD/status identical before/after.
2. **Approval-denied loop produces no mutations**: a policy denying all writes
   + approver denying -> every item ends `planned_denied`; no file changes, no
   git commits, HEAD unchanged; a deny is recorded in audit records.
3. **Full e2e approve-to-commit**: approve apply -> file mutation; verify
   passes; `commit_on_verify` -> `git.stage`/`git.commit` approved -> HEAD
   advances exactly once; task `completed`; working tree clean.
4. **Verify-failure rolls back**: mutation applied, verify tool reports error
   -> 6D rollback restores original file content (checksum match); task
   `rolled_back`; NO git commit occurs.
5. **Runaway cap**: spec with `max_iterations` smaller than item count ->
   loop stops at the cap, remaining items untouched (no mutation), status
   reports capped.
6. **Restart reconciliation**: simulate outage (once state is checkpointed,
   never touch; construct interrupted `running` task) -> `resume()` ->
   reconcile marks/recovers per 6C; remaining items finish; completed items
   are never re-executed (no duplicate file edits / commits).
7. **Journaling**: each gate decision + each task/step transition appears in
   EngineState ops/audit; `run()` returns a deterministic summary with item
   statuses in order.
8. **Path/identity confinement**: items outside workspace_root rejected at
   spec construction; unvalidated task dicts fail closed via
   `engine.validate_task`.
9. **Invariant**: production `knowledge.db` SHA unchanged;
   `api/contract.py` `CONTRACT_VERSION` unchanged; default policy git still
   deny.

## 8. Security checklist
- Every mutation is approval-gated per item at the tool level (5) — deny means
  zero writes.
- No unstructured shell: all execution through TaskEngine tools (git via 7B
  confined spawn; file ops via PathPolicy).
- Bounded: `max_iterations` + `max_duration`; resume cannot re-run completed
  items; no background/daemon loop.
- Ownership (6E) enforced by coordinator on every task claim; rollback (6D)
  only within the same workspace via EngineState history.
- Audit of denials (non-repudiation) and grants.

## 9. Persistence
Tasks/steps/operations/audit in `EngineState`; workflow degenerate outputs
(planned/denied/completed/rolled_back/capped) are task statuses. Production
`knowledge.db` never touched.

## 10. Failure / recovery
Mid-loop failure (verify fail or tool error) -> 6D rollback path or marked
`planned_denied`/failed with stop; restart -> `reconcile()` (6C) then resume
remaining items; completed items have stable step records and are skipped.

## 11. Audit gates
- No-mutation dry-run plan (item 1).
- Deny matrix + zero mutation on denial (item 2).
- E2E approve->commit exactly-once (item 3), verify-failure rollback (item 4).
- Caps (item 5), restart/resume determinism (item 6), journaling (item 7).
- Confinement (item 8) + invariants (item 9).

## 12. Commit checkpoints
- `8A deterministic workflow automation` (after GO audit + operator commit).

## 13. Non-goals
Autonomous execution without a human gate; network scheduling; daemon/background
loops; parallel/concurrent item execution; push/publish; AI proposals (9A); any
new shell surface; changes to 6A/6B/6C/6D/7B semantics.

## 14. Risks
- Over-eager self-correcting loops: capped by `max_iterations` (item 5) and the
  approval-deny default; apply never auto-continues on verify failure (it
  rolls back and stops).
- Resume re-running an in-flight item: mitigated by 6C reconcile semantics and
  `completed`/`rolled_back` step records being skipped (item 6).
- Scope creep into 9A territory: 8A drives only planner-valid task dicts, no
  AI/LLM adapter.

## 15. Definition of Done
- All test items 1-9 green; Layer 6/7 regressions green (full suite).
- E2E workflow in a temp workspace green including outage/resume (item 6).
- Production KB SHA + `CONTRACT_VERSION` unchanged.