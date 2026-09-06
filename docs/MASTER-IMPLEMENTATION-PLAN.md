# Database Tools / AI Engine Project — Master Implementation Plan

Status: PLANNING DOCUMENT (read-only)
Generated: 2026-09-02
Basis: real inspected state of this repository (HEAD `d409126`, worktree inspection, prior Layer 6A audit).

> This document is a **roadmap, not a permission slip**. It defines WHAT must
> be built, in WHAT order, under WHAT protocol. It does **not** authorize any
> agent to autonomously implement future slices, skip audit gates, commit
> without explicit instruction, push, modify the production knowledge
> database, or change the public v1 contract.

---

## 1. Vision Statements

### Top-level vision
Build a deterministic, safe, locally-runnable engineering assistant that
plans, executes, verifies, and corrects codebase changes under a strict
PLAN -> IMPLEMENT -> TEST -> AUDIT -> FIX -> RE-AUDIT -> COMMIT -> CHECKPOINT
protocol. Every mutation is journaled, approval-gated, rollback-capable,
and persisted in a way that survives crashes and restarts. All behavior
remains deterministic and auditable; AI (if ever added) stays behind an
optional, isolated boundary.

### Per-layer vision
- **Layer 6 — Execution coordinator & persistence:** a planner can persist a
  task, a coordinator can claim it, run every step through the existing
  permission/journal/verification pipeline, persist step outcomes, recover
  after a crash/restart, and roll back a whole task on failure.
- **Layer 7 — Git:** first-class, workspace-bounded, approval-gated read and
  write Git operations that produce journaled audit records — with `push`
  explicitly denied by default.
- **Layer 8 — Automation:** deterministic, human-gated batch workflows that
  reuse the same coordinator (fix -> apply -> verify -> commit), with an audit
  gate per slice.
- **Layer 9 — Optional AI boundary (LAST):** an isolated adapter that turns AI
  output into *proposed*, validated, provenance-tagged plans that still pass
  through the fully deterministic, permission-gated pipeline. AI never touches
  SQLite internals, project files, the shell, or production knowledge.

---

## 2. Current Architecture Summary (as inspected)

Runtime flow today:

```
Planner (tools/planner/deterministic.py)  -- deterministic intent -> tool plan
     |
     v
TaskEngine (engine/task_engine.py)        -- IN-MEMORY step runner (no persistence)
     |
     v  per step
ExecutionRunner (tools/coding/exec_tools.py)  -- allowlist, hardened run
ApprovalGate (tools/permissions/approvalgate.py) -- read/write/execute/git deny/approval
WriteRunner (tools/coding/write_tools.py)      -- snapshots + journaled writes
RollbackExecutor (tools/permissions/rollback.py) -- journaled rollback of ops
     |
     v
EngineState (tools/permissions/journal.py) -- database/engine_state.db
     meta, operations, audit, snapshots, tasks, task_steps (6A)
```

Key modules inspected and their role:

- `tools/permissions/journal.py` — EngineState; `database/engine_state.db`
  (configurable); tables `meta`, `operations`, `audit`, `snapshots`, and
  (Layer 6A, uncommitted) `tasks` + `task_steps`. `PRAGMA foreign_keys = ON`,
  `PRAGMA busy_timeout = 5000`. Schema stamp `ENGINE_STATE_SCHEMA_VERSION = "2"`.
- `tools/permissions/rollback.py` — journaled, deterministic rollback of
  operations (restore snapshot / undo write).
- `tools/permissions/approvalgate.py` — read/write/execute/git/network/publish
  domains. `GIT`, `NETWORK`, `PUBLISH` are blocked (deny) at the gate.
- `tools/permissions/policy.py` — validated policy; `git: deny`, `network: deny`,
  `publish: deny` by default; `env_strip` secrets.
- `tools/permissions/execution.py` — hardened command execution
  (`run_checked`: env filtering, output caps, process-group kill, memory limits).
- `tools/permissions/decisions.py` — decision types + stable reason codes.
- `tools/permissions/audit.py` — persisted audit records.
- `tools/permissions/pathpolicy.py` — workspace-bounded path rules.
- `engine/task_engine.py` — in-memory TaskEngine; statuses
  completed/failed/completed_with_errors/planned/planned_with_errors/invalid;
  step statuses success/failed/planned/skipped; `DEFAULT_MAX_STEPS = 100`,
  `DEFAULT_MAX_DURATION = 300`; fixed tool registry; `$ref` step chaining;
  currently storing nothing to `database/engine_state.db`.
- `tools/planner/deterministic.py` — `PLANNER_VERSION = "1"`,
  `ALLOWED_INTENTS = {bug_fix, test_verify, generic}`, 16 allowed tools,
  forbidden write/knowledge/rollback tools, evidence classification.
- `tools/verification/__init__.py` — Layer 5 diag parser + FixProposal +
  deterministic fix-rule dispatcher (read-only).
- `tools/indexer/*` — persistent project index at `<workspace>/.ai-engine/project_index.db`
  (incremental, `stable_id`), used by planner/verification/task_engine.
- `api/contract.py` — public v1 tool contract, `CONTRACT_VERSION = "1"`, frozen;
  whole module is transport-independent and read-only for the classic
  knowledge engine.
- `api/session.py` — long-lived knowledge session server (classic engine only).

### Production knowledge invariants (must never change)
- `database/knowledge.db` SHA-256:
  `000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91`
- `PRAGMA integrity_check` -> `ok`; `PRAGMA foreign_key_check` -> `[]`.
- Counts: nodes 4846, relationships 1338, sources 6.
- `api/contract.py` line 85: `CONTRACT_VERSION = "1"`.

---

## 3. Completed Checkpoints

Commit map (most recent first), layer -> commit:

| Layer set | Commit | Description |
|---|---|---|
| 5B integration | `d409126` | Deterministic Layer 5B verification fixes and planner integration (HEAD) |
| 5B-1a | `14944d7` | E302 fix-rule foundation |
| 5A-2 | `30492b2` | flake8/Ruff verification coverage |
| 5A-1 | `0e77f54` | parser foundation |
| 4D-1 | `02eaada` | Android/Termux memory safety |
| 4C | `2dbd334` | pip inspection tooling |
| 4B | `f5a31f1` | npm test/build tooling |
| 4A | `48f9e1e` | lint/formatter tooling |
| 6-planner part | `c62807a` | deterministic planner (Layer 6 planning-side work) |
| 2 | `2237595` | incremental project index |
| 2 | `c9b2518` | persistent code index |
| 1B | `44d00c6` | journal-based rollback |
| 1 | `0f90a67` | safety/permission foundation |

Layer 5 tests verified green: 52 (task_engine+planner+fix_planner), 59
(fix_rules + fix_rules_full), 112 (rollback + permissions + contract_v1),
34 (engine_state_tasks, 6A). 

One known unrelated failure (verified identical on pristine HEAD): 
`tests.test_tools.ContractValidationTests.test_operation_catalog_is_stable`
(an 8-element frozenset being asserted `is not false`). It is off-roadmap,
pre-existing, and untouched by Layer 6.

---

## 4. Current Checkpoint

- **Committed baseline:** `d409126` (rebaselined 5B integration). Branch
  `master`, remote configured, 14 commits ahead of origin, nothing pushed.
- **Working tree (git status --short):**
  - `M  tools/permissions/journal.py`      — Layer 6A task persistence (uncommitted)
  - `?? tests/test_engine_state_tasks.py`  — 6A test suite (34 tests) (uncommitted)
  - `?? docs/CODING-TOOL-ROADMAP.md`       — original roadmap (untracked)
- **Layer 6A status:** implemented, 34/34 tests green, independent audit
  verdict **GO** (0 CRITICAL, 0 HIGH, 2 MEDIUM, 5 LOW). See Section 7 and
  the audit record for MEDIUM/LOW details.
- **Checkpoint bookkeeping (this document):**
  - Nonce #16 checkpoint = HEAD `d409126`; 6A lives in the working tree.
  - Recommended next commit (only when the user explicitly asks): commit 6A
    (journal.py + tests/test_engine_state_tasks.py) after re-audit, then create
    the 6B slice plan below. **No auto-commit is authorized by this document.**

---

## 5. Remaining Roadmap

| Layer | Title | State |
|---|---|---|
| 6A | Task + step persistence in EngineState | Implemented (worktree), audited GO — commit pending |
| 6B | Persistent Execution Coordinator | Planned (next slice) |
| 6C | Crash recovery / restart reconciliation | Planned |
| 6D | Task-level rollback aggregation | Planned |
| 6E | Concurrency & ownership hardening | Planned |
| 6F | Read-only status/list operator surface | Optional |
| 7A | Git status/diff/log inspection tools | Planned |
| 7B | Git commit/stage write tools (approval-gated) | Planned |
| 7C | Push/publish prerequisites | Optional; denied by default |
| 8A | Automation workflows over the coordinator | Planned |
| 9A | Optional AI proposal boundary | Last / optional |

---

## 6. Dependency Graph

```
1   Safety/permission foundation (0f90a67)      [DONE]
1B  Journal + rollback (44d00c6)                [DONE]
2   Persistent + incremental code index          [DONE]
4A/4B/4C/4D-1  lint/build/packaging tooling     [DONE]
5A-1/5A-2/5B-1a/5B  verification + planner      [DONE]
6A  Task/step tables (EngineState)              [WORKTREE, GO]
    |
    +--> 6B Persistent Execution Coordinator    -> 6C crash recovery
    |                                              -> 6D task rollback
    |                                              -> 6E concurrency/ownership
    |                                              -> 6F operator status (opt)
    |
7A git read tools --> 7B git write tools (approval-gated) --> 7C push (denied)
8   Automation reuses 6B coordinator + 7 git + 5 verification
9   AI boundary consumes 6B PLAN output as proposals only
```

Edge rule: `7B` and `8` depend on `6B` (a persistent coordinator that can
drive multi-step change workflows with per-step approval). `9` depends on all
of 5, 6, 7, 8 because its proposals must ride the deterministic pipeline.

---

## 7. Global Protocol, Invariants, and Non-Goals

### The universal slice protocol
Every slice (and the document itself) follows:

```
PLAN -> IMPLEMENT -> TEST -> AUDIT -> FIX(only if required) -> RE-AUDIT -> COMMIT -> CHECKPOINT
```

- **PLAN:** write a concrete slice plan (files, tests, invariants, messages)
  before touching code.
- **IMPLEMENT:** minimal, idiomatic change against the actual repository.
- **TEST:** run the slice tests plus the full regression set.
- **AUDIT:** read-only review of the diff, schema, state machine, concurrency,
  and invariants; assign severity (CRITICAL/HIGH/MEDIUM/LOW).
- **FIX:** address CRITICAL/HIGH before proceeding; MEDIUM/LOW are recorded
  and scheduled (never silently dropped).
- **RE-AUDIT:** confirm the diff closed as intended and nothing else changed.
- **COMMIT:** only when the user explicitly asks; commit message links to the
  slice id and checkpoint (e.g. `6B Persistent Execution Coordinator`).
- **CHECKPOINT:** record the resulting HEAD + worktree truth in the "Current
  Checkpoint" section.

### Global invariants (applies to every slice)
1. Never modify `database/knowledge.db` or its SHA; never run DDL/DML against
   it from any coding-tool layer.
2. Never modify `api/contract.py` semantics; `CONTRACT_VERSION = "1"` stays.
3. Never modify `docs/CODING-TOOL-ROADMAP.md`.
4. Never bypass `ApprovalGate`, `Policy`, `PathPolicy`, or `ExecutionRunner`.
5. Every mutation must be journaled in `database/engine_state.db` (or the
   configured state DB) and be rollback-capable.
6. No horizontal privilege: a coordinator may only act through approved tools
   on approved paths inside the workspace it was configured for.
7. No `push` and no `publish` unless explicitly re-authorized; defaults are
   deny.
8. Do not commit runtime artifacts: `.ai-engine/`, `engine_state.db`,
   `project_index.db`, backups, `*.pyc`, WAL/SHM files.
9. Tests use isolated temp directories with explicit `db_path=` — never the
   production `database/` directory.
10. Any planned behavior that cannot be justified from inspected code is
    marked `OPEN DECISION`, never silently invented.

### Non-goals (this roadmap explicitly does NOT include)
- An autonomous background scheduler that executes slice code without a human
  gate.
- Direct access to SQLite internals from AI.
- Changing the classic knowledge engine's contract or ingestion pipeline.
- Network/publish/telemetry surfaces of any kind (denied).

---

## 8. Security Model

Layers of control, all preserved and extended:

1. `Policy` — capabilities and per-command spec; secrets stripped from env.
2. `PathPolicy` — workspace confinement; writes outside workspace denied.
3. `ApprovalGate` — read/write/execute/rollback/git/network/publish domains;
   GIT/NETWORK/PUBLISH denied unless explicitly configured and gated by
   operator approval.
4. `ExecutionRunner.run_checked` — allowlisted executables, closed arg forms,
   cwd confinement, timeouts, output caps, process-group kill, memory cap.
5. `WriteRunner` — snapshot before write, journaled, approval-gated writes.
6. `RollbackExecutor` — deterministic rollback from the journal.
7. `EngineState` — single source of truth for tasks, steps, operations, audit.
8. Layer 9 stub — AI output can only become a *proposal*; it never executes.

### Layer-specific security additions
- **6B/6E:** owner-token ownership check on every post-claim transition;
  bounded retry/backoff for `database is locked` (see MEDIUM findings).
- **7A/7B:** Git tools are real tools with their own allowlist and approval
  rules; commit policy must reject staging files outside the workspace and
  never auto-`--push`.
- **9A:** AI boundary has no filesystem/shell/socket privileges at all.

---

## 9. Persistence Model

- All engineering-state lives in `database/engine_state.db` (configurable via
  `db_path`), opened with `foreign_keys = ON`, `busy_timeout = 5000`,
  single-writer coordination assumed; default SQLite journal mode (WAL is an
  `OPEN DECISION` for 6E).
- Tables: `meta`, `operations` (journal), `audit`, `snapshots`, and (6A)
  `tasks` + `task_steps` with FK cascades and CHECK constraints.
- `schema_version` stamped in `meta` as `"2"` (6A). No "1" recorded — see LOW.
- Index artifacts live at `<workspace>/.ai-engine/project_index.db` (runtime,
  never committed).
- The production knowledge DB (`database/knowledge.db`) is strictly read-side
  for classic knowledge queries; coding-tool layers never open it for writes.
- Crash recovery uses the persisted task/step state as the source of truth
  (see 6C).

---

## 10. Testing Strategy

- Add tests in the same style as the existing suites:
  `tests/test_engine_state_tasks.py` (34, green), `tests/test_rollback.py`,
  `tests/test_permissions_*.py`, `tests/test_task_engine.py`,
  `tests/test_planner.py`, `tests/test_fix_planner.py`,
  `tests/test_fix_rules.py`, `tests/test_fix_rules_full.py`, knowledge suite.
- Every slice test must use an isolated temp `db_path` and, where relevant, a
  temp workspace root.
- State-machine transitions are tested exhaustively (valid + invalid
  transitions), same discipline used for 6A (34 test matrix).
- Concurrency tests assert deterministic claim ownership and no partial
  double-claim.
- Full regression battery must stay green: 34 + 52 + 59 + 112 and any new
  suites.
- The known unrelated failure (`test_operation_catalog_is_stable`) is tracked
  separately and is not a gate for Layer 6-9 work.

---

## 11. Audit Protocol

- Every slice gets an independent, read-only audit before commit and again
  after any FIX step (RE-AUDIT).
- Severity policy: CRITICAL/HIGH must be fixed before the slice may be
  hardened; MEDIUM/LOW are recorded with a scheduled slice.
- Audit checklist per slice: schema vs constants; migration idempotency;
  task/step state machine completeness; claim/concurrency ownership; tx
  integrity; JSON canonicals; EngineState compatibility; security/isolation;
  test green; production invariants (SHA/counts/contract); git/artifact state.
- Audit evidence is temp-file isolated and cleaned up afterwards.

### Open 6A findings carried into Layer 6 planning (from GO audit)
- **MEDIUM-1:** `busy_timeout=5000` can expire under a pathological 100-thread
  write storm, letting a bare `sqlite3.OperationalError: database is locked`
  escape. Schedule: 6E (or a dedicated 6B hardening sub-slice).
- **MEDIUM-2:** `owner_token` is set at claim but not re-verified on post-claim
  transitions. Schedule: 6E.
- **LOW x5:** pre-existing wrong-shaped tables tolerated by `IF NOT EXISTS` +
  stamped `"2"`; idempotent-create ignores differing args and echoes requested
  vs stored status; `list_task_steps` tie-order not spec-guaranteed;
  `_canonical_json` default `allow_nan=True`; `schema_version "2"` without an
  archived `"1"`. These are low-risk and fold into normal hardening.

---

## 12. Commit Protocol

- Commit only when explicitly instructed by the user/operator.
- Inspect `git status`, `git diff`, and `git log --oneline -10` before each
  commit; stage only intended files.
- Never stage DB artifacts (`database/*.db*`, backups) or `.ai-engine/`.
- Never commit secrets; `env_strip` and `.gitignore` cover the known patterns.
- Commit messages follow repo style, e.g.:
  `6B Persistent execution coordinator (plan -> claim -> steps -> journal)`.
- Never `git push` unless explicitly requested.
- After commit: update "Current Checkpoint" with the new HEAD.

---

## 13. Recovery Protocol (crash / restart)

- On (re)start, the coordinator reconciles persisted state against the
  journal before doing any new work.
- Orphaned in-flight tasks/steps (planned but not finalized) are marked
  `failed` / `skipped` with a deterministic reason (e.g. `interrupted_restart`)
  — never auto-retried without an operator decision.
- Orphaned operations the journal knows about are candidates for
  task-level rollback (6D) at the operator's explicit request; they are never
  auto-undone during restart.
- `integrity_check` is run when a state DB is suspected fnder; corruption
  means fail closed to a read-only report, never ignore-and-continue.
- Restart itself never mutates the production knowledge DB.

---

## 14. Definition of Done (per layer)

A layer is done when:
- every slice is committed at its checkpoint (or explicitly deferred), with
  the exception of slices the user keeps uncommitted by choice;
- a fresh checkout + full regression battery is green;
- an audit produced GO with no open CRITICAL/HIGH and an explicit list of
  open MEDIUM/LOW (each scheduled or explicitly won't-fix);
- data invariants (SHA, counts, contract v1) hold;
- the roadmap's "Current Checkpoint" and "Open architecture decisions"
  sections are updated;
- nothing was pushed unless explicitly requested.

---

## 15. Layer-by-Layer Plan (Layers 6-9)

> Per layer: (1) purpose, (2) current state, (3) gap, (4) architecture,
> (5) dependencies, (6) slices, (7) tests, (8) security, (9) persistence,
> (10) failure/recovery, (11) audit gates, (12) commit checkpoints,
> (13) non-goals/exclusions, (14) risks, (15) Definition of Done.

### Layer 6 — Execution coordinator & persistence

**6A — Task + step persistence in EngineState [WORKTREE, audited GO]**
1. Purpose: persist planner output and step outcomes durably so the process
   no longer depends on in-memory TaskEngine state.
2. Current state: `ENGINE_STATE_SCHEMA_VERSION = "2"`; `TASK_STATUSES`,
   `TASK_TERMINAL_STATUSES`, `TASK_STEP_STATUSES`, `TASK_TRANSITIONS`,
   `TASK_STEP_TRANSITIONS`; `_TASK_SCHEMA` -> `tasks`, `task_steps` (PK/FK
   `task_id` CASCADE, CHECKs); `_ensure_task_tables`; methods
   `create_task`, `get_task`, `update_task_status`, `update_task_result`,
   `claim_task`, `ensure_task_step`, `get_task_step`, `list_task_steps`,
   `update_task_step_status`, `update_step_result`, `attach_operation`,
   `_canonical_json`. Companion suite `tests/test_engine_state_tasks.py`.
3. Gap: none functionally; commit is pending explicit instruction.
4. Architecture: EngineState owns task tables alongside journal/audit.
5. Dependencies: journal.py Phase 1B core.
6. Slices: none remaining (single slice).
7. Tests: 34/34 green; concurrency subset 5x green; regressions green.
8. Security: FK+CASCADE, deterministic transactions, no bypass.
9. Persistence: `database/engine_state.db` (`tasks`, `task_steps`).
10. Failure/recovery: transactional; any part failing rolls back the tx.
11. Audit gates: GO (0 CRITICAL/0 HIGH/2 MEDIUM/5 LOW) — see Section 11.
12. Commit checkpoint: `6A task and step persistence in EngineState` (pending
    explicit commit instruction).
13. Non-goals: no coordinator, no recovery, no list API changes in this slice.
14. Risks: low; MEDIUM findings deferred to 6E by design.
15. DoD: committed at its checkpoint + regressions green + invariants hold.

**6B — Persistent Execution Coordinator [NEXT SLICE]**
1. Purpose: a durable coordinator that turns a planned task into claimed
   task + per-step execution with persistence and journal attachment.
2. Current state: planner emits plans; TaskEngine runs in memory; EngineState
   (6A) can store tasks/steps; ApprovalGate+RollbackExecutor exist. There is
   **no** durable line from plan to execution and no persistent coordinator.
3. Gap: connect planner -> EngineState -> TaskEngine-style execution -> journal
   with a single task identity throughout.
4. Architecture (OPEN DECISION: new `engine/coordinator.py` driving TaskEngine
   vs extending `engine/task_engine.py`; recommended: new coordinator that
   reuses TaskEngine as the pure execution driver). Flow:
   `plan -> create_task(planned) -> claim(owner) -> per step:
   persist step planned -> ApprovalGate -> ExecutionRunner/WriteRunner ->
   journal op (attach_operation) -> verification (fix proposals optional) ->
   update_step_result -> final task status`.
5. Dependencies: 6A tables, planner, task_engine, execution, approvalgate,
   rollback, verification.
6. Slices: one slice `6B` (core coordinator) + optional `6B-hardening`
   sub-slice folding in MEDIUM-1 busy_timeout strategy if not deferred to 6E.
7. Tests: unit (plan->claim->step->result lifecycle), integration
   (planner -> coordinator -> ApprovalGate -> journal), invalid-transition
   rejection, temp dirs only.
8. Security: only registered tools; deny-by-default; owner checks; all writes
   via WriteRunner snapshots; secrets stripped.
9. Persistence: every task/step status visible in EngineState; every op in
   journal.
10. Failure/recovery: a step crash leaves step `in_progress` + journal as the
    truth; recovery handled by 6C.
11. Audit gates: schema, transitions, ownership, tx, invariants, git state.
12. Commit checkpoint: `6B Persistent execution coordinator`.
13. Non-goals: no crash recovery (next), no task rollback (next), no list API.
14. Risks: MEDIUM-1/2 must not regress; keep in-memory TaskEngine behavior
    byte-identical when running persisted tasks.
15. DoD: full lifecycle green incl. claim ownership test; regressions green.

**6C — Crash recovery / restart reconciliation**
1. Purpose: on restart, reconciliation of persisted tasks against journal.
2. Current state: none — no coordinator exists yet (6B first).
3. Gap: deterministic orphan detection + failure marking without auto-retry.
4. Architecture: on start, scan `tasks`/`task_steps`; mark in-flight as
   `failed`/`skipped` with reason `interrupted_restart`; never auto-retry.
5. Dependencies: 6B.
6. Slices: `6C` core + `6C-hardening` if audits reveal corner cases.
7. Tests: simulated crash (kill between steps) then restart; verify state.
8. Security: reconciliation is read-then-write under ownership; no bypass.
9. Persistence: uses EngineState only.
10. Failure/recovery: this slice IS the recovery.
11. Audit gates: idempotency of reconciliation (restart twice == once).
12. Commit checkpoint: `6C crash recovery and restart reconciliation`.
13. Non-goals: automatic resumption without operator decision.
14. Risks: over-credulous recovery; keep strict.
15. DoD: two consecutive restarts produce identical final state.

**6D — Task-level rollback aggregation**
1. Purpose: roll back every journaled operation belonging to a task when a
   required step fails.
2. Current state: single-op RollbackExecutor exists; no aggregation.
3. Gap: manifest build (`list_task_operations` via task step -> attach_operation)
   + ordered rollback + task status `failed` with rollback evidence.
4. Architecture: coordinator asks operator (or configured policy) then
   aggregates ops for task, uses RollbackExecutor in reverse journal order.
5. Dependencies: 6B, rollback.EngineState operations table.
6. Slices: `6D`.
7. Tests: multi-step task w/ one bad step -> all ops rolled back; order stable.
8. Security: rollback is itself an approval-gated, journaled action.
9. Persistence: rollback records appended to audit; task marked failed.
10. Failure/recovery: partial rollback is impossible by design (journal replay).
11. Audit gates: ordering determinism + snapshot restore verification.
12. Commit checkpoint: `6D task-level rollback aggregation`.
13. Non-goals: compensating new writes (only undo).
14. Risks: unexpected content drift between snapshot and rollback time.
15. DoD: restore-on-failure proven by test for each tool kind.

**6E — Concurrency & ownership hardening**
1. Purpose: close MEDIUM-1 (busy_timeout storm) and MEDIUM-2 (owner_token not
   checked post-claim).
2. Current state: claim races tested (5x green); owner stored but unused.
3. Gap: enforce owner on transitions; bounded retry/backoff or serialized
   writer for `database is locked`; decide WAL (OPEN DECISION).
4. Architecture: coordinator checks `owner_token` on every transition;
   writes retried with cap; single logical writer per DB enforced.
5. Dependencies: 6B.
6. Slices: `6E` (owner) + `6E-db` (lock strategy).
7. Tests: 100-thread storm -> no bare OperationalError; wrong-owner transition
   rejected.
8. Security: ownership is the core isolation guarantee.
9. Persistence: unchanged schema.
10. Failure/recovery: lock retry capped then fail closed with audit.
11. Audit gates: stress + ownership matrix.
12. Commit checkpoint: `6E concurrency and ownership hardening`.
13. Non-goals: distributed coordination (single host only).
14. Risks: WAL introduces new artifacts to ignore in commits.
15. DoD: MEDIUM-1/2 re-audited LOW or closed.

**6F — Read-only status/list operator surface [OPTIONAL]**
1. Purpose: `list_tasks(status=...)`, task summaries, step listing for
   operators — no mutation.
2. Current state: `get_task`, `list_task_steps` exist; no filtered list.
3. Gap: filter + summary surface.
4. Architecture: thin EngineState read methods + optional CLI/print.
5. Dependencies: 6B.
6. Slices: `6F`.
7. Tests: filtering + ordering determinism.
8. Security: read-only domain only.
9. Persistence: none beyond reads.
10. Failure/recovery: n/a (read-only).
11. Audit gates: no hidden mutation paths.
12. Commit checkpoint: `6F read-only task status surface`.
13. Non-goals: operator mutation endpoints.
14. Risks: low.
15. DoD: green reads; no writes observed via audit.

### Layer 7 — Git (no git tooling exists today)

**7A — Git status/diff/log inspection tools**
1. Purpose: deterministic, workspace-bounded read-only Git inspection.
2. Current state: <none>. `policy.py` sets `git: deny`; ApprovalGate denies
   Domain.GIT outright.
3. Gap: dedicated read tools that parse `git status --porcelain`,
   `git diff --no-ext-diff`, `git log --oneline` via a strict allowlist
   (no arbitrary shell).
4. Architecture: `tools/git/` module; allowlist of `git` subcommands with
   closed arg forms; workspace confinement; deterministic ordering.
5. Dependencies: Layer 6 (coordinator aware of git op results), approvalgate.
6. Slices: `7A`.
7. Tests: fixture repo in temp; porcelain parsing; escaping cwd; arg
   rejection.
8. Security: reads only; no commit config mutation; cwd confined.
9. Persistence: results journaled as `audit` records only.
10. Failure/recovery: parse-safe on missing repo/config.
11. Audit gates: arg validation matrix; path confinement.
12. Commit checkpoint: `7A git inspection tools`.
13. Non-goals: commits, staging, push in this slice.
14. Risks: porcelain version drift; parse defensively.
15. DoD: read-only git surface reviewed + green.

**7B — Git commit/stage write tools (approval-gated)**
1. Purpose: stage/commit under approval, journaled.
2. Current state: <none>; GIT domain denied.
3. Gap: `git add <paths>`, `git commit -m <msg>` with explicit policy
   (paths bounded to workspace; no `--all` beyond policy; no secrets; no push).
4. Architecture: like 7A but requires approval; every command becomes a
   journaled op; commit config never auto-set.
5. Dependencies: 7A, 6B coordinator, approvalgate (git allow only when
   configured), execution allowlist.
6. Slices: `7B`.
7. Tests: deny-by-default; approval path works; refusal to stage outside
   workspace; no push.
8. Security: GIT domain still denied unless policy explicitly opens it.
9. Persistence: journaled operations + audit.
10. Failure/recovery: commit failure leaves clean state; no auto-amend.
11. Audit gates: exactly-once commit semantics (no duplicate commits).
12. Commit checkpoint: `7B git staging and commit tools`.
13. Non-goals: push, force, amend, hooks changes.
14. Risks: broad `git add` — strictly bounded by explicit paths.
15. DoD: approved commit applies exactly once; unapproved denied.

**7C — Push/publish prerequisites [OPTIONAL / DENIED by default]**
1. Purpose: document what a future push surface would require.
2. Current state: publish domain denied.
3. Gap: n/a unless an explicit architecture decision opens it.
4. Architecture: (none this banner) revisit only with operator decision.
5. Dependencies: 7B.
6. Slices: deferred / `OPEN DECISION`.
7. Tests: n/a until approved.
8. Security: remains deny-by-default.
9. Persistence: n/a.
10. Failure/recovery: n/a.
11. Audit gates: explicit allow + approval.
12. Commit checkpoint: only after explicit authorization.
13. Non-goals: auto-push of any kind.
14. Risks: credential exposure; kept off the default path.
15. DoD: acceptance criteria agreed before any implementation.

### Layer 8 — Automation

**8A — Deterministic workflow automation over the coordinator**
1. Purpose: batch/scripted change workflows (fix proposals -> apply ->
   verify -> commit) using the same gates.
2. Current state: <none>; no scheduler/loop exists except deterministic tools.
3. Gap: a bounded loop that plans/executes slices of work, submits for human
   approval at configured gates, journals everything.
4. Architecture: workflows are deterministic state machines driving 6B +
   verification + 7B; each workflow iteration is a task with steps and audit.
5. Dependencies: 6B, 5 verification/fix rules, 7B git.
6. Slices: `8A`.
7. Tests: dry-run loops; approval-denied loops produce no mutations.
8. Security: every loop iteration is approval-gated; no unstructured shell.
9. Persistence: tasks/steps/operations/journal.
10. Failure/recovery: loop restart reconciles along 6C/6D.
11. Audit gates: per-iteration checkpoint; runaway loop caps.
12. Commit checkpoint: `8A deterministic workflow automation`.
13. Non-goals: autonomous execution without a human gate; network scheduling.
14. Risks: over-eager self-correcting loops — cap iterations and require
   approval after verify.
15. DoD: e2e workflow in temp workspace green incl. outages and re-runs.

### Layer 9 — Optional AI boundary (LAST)

**9A — AI proposal adapter (isolated, optional)**
1. Purpose: let AI contribute *proposed* plans that still pass the full
   deterministic, permission/approval pipeline.
2. Current state: <none>; the repo's classic knowledge engine is read-only
   and unrelated to coding-tool execution; no model calls exist.
3. Gap: an adapter that turns AI output into `PLAN` proposals with
   confidence/provenance, validated against ALLOWED_INTENTS/ALLOWED_TOOLS.
4. Architecture: AI boundary has no filesystem/shell/socket access; produces
   proposal objects; a validator maps them onto existing planner contract;
   only then does the coordinator attempt approval/execution; deterministic
   fallback when AI unavailable/over threshold.
5. Dependencies: 5, 6B, 7B, 8A as ride-along.
6. Slices: `9A` adapter + `9A-validation`.
7. Tests: proposal rejection paths; threshold fallback; no side effects on AI
   strand.
8. Security: AI cannot bypass permissions, approval, journal, or the v1
   contract; cannot mutate knowledge.db or engine_state except via tools.
9. Persistence: proposals journaled with provenance; never auto-executed.
10. Failure/recovery: AI outage degrades to planner-only pipeline.
11. Audit gates: every accepted proposal links to its provenance/confidence.
12. Commit checkpoint: `9A optional AI proposal boundary`.
13. Non-goals: autonomous AI; AI w/ shell/filesystem/SQLite; AI training on
    project data without explicit operator decision.
14. Risks: prompt/provenance spoofing — validator must not trust free text.
15. DoD: proposal pipeline green; AI strand provably side-effect-free.

---

## 16. Open Architecture Decisions

1. `OPEN DECISION` — 6B module placement: new `engine/coordinator.py` (recommended)
   vs extending `engine/task_engine.py` deeper, vs adding to `tools/`.
2. `OPEN DECISION` — WAL journal mode for `engine_state.db` as part of 6E
   (gains concurrency, adds runtime artifacts to ignore/clean).
3. `OPEN DECISION` — 6E MEDIUM-1 strategy: bounded retry/backoff vs serialized
   single-writer vs both.
4. `OPEN DECISION` — 6F scope: include list_tasks now or defer until an
   operator surface is requested.
5. `OPEN DECISION` — 6D rollback policy: auto-aggregate on required-step
   failure vs require operator approval first (recommended: require approval).
6. `OPEN DECISION` — Layer 7 `git` deny-by-default exceptions: is a policy
   override ever acceptable, or read-only forever? (Recommended: allow only
   via explicit policy + approval in 7B.)
7. `OPEN DECISION` — Layer 8 automation breadth: which workflow classes are
   in scope for 8A, and iteration caps.
8. `OPEN DECISION` — Whether any future AI (9A) may consume the read-only
   classic knowledge engine (via api.tools/session only).

---

## 17. CURRENT NEXT ACTION

> This section is now maintained by the deterministic workflow controller
> (`engine/roadmap`, invoked via the `/next` command). It is no longer
> hand-edited, and it grants no implementation authority.

- **Mechanism:** the next valid project action is computed by
  `python3 -m engine.roadmap next` from the roadmap manifest, the persisted
  workflow state (`.workflow/state.json`), and git facts. The controller is
  approval-driven and fail-closed; every action marked
  `requires_human_approval: true` is a hard STOP boundary.
- **Current computed next action (as of this checkpoint):** per `.workflow/
  state.json`, slice 6A is `audited_go` but uncommitted, so the next action is
  `REQUEST_APPROVAL` step `commit` for slice 6A — request operator approval to
  COMMIT + CHECKPOINT 6A. After that checkpoint, the controller will advance
  to slice 6B (`prepare plan` → request approval) automatically.
- **Do NOT** implement 6C-6F, Layer 7, Layer 8, or Layer 9 slices ahead of the
  computed next action.
- **Do NOT** commit the uncommitted 6A working-tree changes (journal.py +
  tests/test_engine_state_tasks.py) unless the operator explicitly approves.
- **Do NOT** commit, push, or modify `database/knowledge.db` /
  `api/contract.py` at any point.