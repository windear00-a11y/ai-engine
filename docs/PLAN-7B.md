# Slice 7B — Git stage/commit write tools (approval-gated) — implementation plan

Slice-format plan per `docs/MASTER-IMPLEMENTATION-PLAN.md` §7 and Section 7B.

## 1. Purpose
Approval-gated, exactly-once, journaled `git stage` + `git commit` as engine
tools. Every git write requires an explicit approver decision; nothing runs
without it. Push, force, amend, config/hooks mutations stay impossible.

## 2. Current state
- 7A added read-only `GitTools` (status/diff/log) with a closed allowlist.
- `Policy.git` defaults to `"deny"`; `ApprovalGate.check(domain=Domain.GIT,...)`
  hard-denies at `approvalgate.py:130-135` regardless of config.
- No git write tool exists; the engine's only writes are file ops via
  `authorize_write_detailed` + `complete_operation`.

## 3. Gap being closed
A first-class, approval-gated surface for `git add <paths>` and
`git commit -m <msg>` that:
- stays deny-by-default unless a policy with `git: allow` is configured AND
  an approver grants the specific proposal;
- bounds every staged path to the workspace;
- records each git write as a journaled op + audit (Layer 6/5 gates);
- guarantees exactly-once commit semantics (no duplicate commits);
- never pushes, amends, forces, touches hooks/config, or shells out.

## 4. Architecture
Extend `tools/git/__init__.py` with a write-capable pair wired to the gate:

- `GitTools.__init__(workspace_root, permissions=None)` — reuse the 7A spawn/
  confinement plumbing; add `self.permissions` (an ApprovalGate).
- **New gate API** `ApprovalGate.authorize_git(proposal)` (commit/prune? no):
  mirrors `authorize_rollback`: returns `(allowed, decision, error)`. It
  consults `self.path_policy.policy.git == "allow"` first (config gate); if
  denied -> deny(REASON_GIT_DENIED). If allowed -> `approver(proposal)`; the
  exact proposal bytes (command+targets) are audited with a fresh operation id.
  The bare `check(Domain.GIT, ...)` deny path at approvalgate.py:130 is
  LEFT UNTOUCHED (defense in depth: arbitrary GIT domain checks stay denied).
- **`git.stage(paths)`** — `git add -- <abs paths>`; paths validated by the
  existing `_validate_rel_path` (reject absolute/traversal/none), each bounded
  to root. Requires `authorize_git` approval of the staged set. On approval,
  runs `git add -- <paths...>` once; `--all` is NEVER used; returns a
  deterministic manifest `{paths:[...], staged:[...]}` (paths may already be
  tracked/unchanged -> staged list is what `git add` reported via
  `git status --short` delta, defensive).
- **`git.commit(message)`** — `git commit -m <msg>`; message required
  non-empty (< 200 chars, newline-stripped). Approver grants the exact message.
  Commit config NEVER auto-set (git fails closed if user.* unset — caller sets
  it via the normal repo setup). On success returns `{commit: <short hash>}`.
  Exactly-once: `git commit` itself is atomic (returns the new HEAD once); if
  the result is ambiguous the tool reports the working tree is clean (nothing
  staged → commit fails → no duplicate).
- **Journaling:** each approved git write goes through the SAME
  `authorize_*` audit path as file writes — every decision (grant/deny) is
  written to the `audit` table with a fresh operation id. Rollback of a git
  commit is explicitly OUT of scope (non-goal; commits are append-only history).

Dry-run posture: staging/committing are MUTATING tools in the 7A/engine sense —
plans mark `git.stage` / `git.commit` as mutating so dry-run tasks plan them
without executing (extend `TaskEngine.MUTATING`).

## 5. Dependencies
7A (confined spawn + path validation), 5 (ApprovalGate + audit + journal),
6B (coordinator persists tool result/status), Policy.git field (exists).

## 6. Scope / files
- **Edit:** `tools/git/__init__.py` — add `permissions` wiring, `stage`,
  `commit`, and reject `push`/`force`/`amend` via the `__getattr__` safety net.
- **Edit:** `tools/permissions/approvalgate.py` — add `authorize_git`
  (config-gated + approver) and an `_audit(Domain.GIT, ...)` record; the
  existing `check(Domain.GIT,...)` deny stays intact.
- **Edit:** `engine/task_engine.py` — pass `permissions` into `GitTools`,
  register `git.stage`/`git.commit`, add both to `MUTATING`.
- **New:** `tests/test_git_write_tools.py`.
- **No changes** to `docs/CODING-TOOL-ROADMAP.md`, `api/*`, `database/*`,
  `policy.py` (git stays deny in default policy).

## 7. Tests (test matrix)
1. Deny-by-default: no policy git-config / policy `git: deny` + default
   approver -> `stage`/`commit` return denied BEFORE any subprocess write
   (audit records the denial).
2. Policy `git: allow` but approver returns False -> denied, no write, no
   mutation of working tree.
3. Full approval path: policy `git: allow`, approver True -> stage an
   untracked file then commit; commit hash matches `git rev-parse HEAD`;
   working tree clean.
4. Exactly-once: empty commit attempt (nothing staged) fails closed with NO
   new commit created (HEAD unchanged); re-commit after success creates no
   duplicate.
5. Path confinement: stage with absolute path / `..` traversal / outside-root
   rejected pre-spawn; `--all` never accepted (rejected arg form).
6. Message validation: missing / empty / >200-char / newline message rejected
   pre-approval.
7. `git.push` / `git.amend` / `git.force` / `git.checkout` are NOT registered
   and `__getattr__` returns the closed error (never executes).
8. Journal/audit: each approved git write produces an audit row with the exact
   proposal (targets/message) + operation id; denied writes also audited.
9. Engine integration: coordinator task with an approved `git.commit` step
   persists success; `MUTATING` contains both tools (dry-run marks them
   planned, never executes).
10. Invariant: production `knowledge.db` SHA unchanged.

## 8. Security checklist
- `authorize_git` double-gated: policy capability AND live approver decision.
- Bare GIT-domain check path stays deny (no silent widening).
- Paths bounded; no `--all`; explicit-args only; no shell.
- Commit config never auto-written; no push/amend/force reachable.
- Denied proposals still audited (non-repudiation).

## 9. Persistence
Approval decisions + outcomes in the `audit`/`journal`-style tables via
`authorize_git`; task step results via the coordinator. Never into the
production `knowledge.db`; never mutates `.git/config`.

## 10. Failure / recovery
Commit failure (e.g. missing identity, empty commit) returns a structured
`ok False` with the git stderr; working tree and HEAD are left exactly as
found (no partial/duplicate commit). No auto-amend, no retry loop.

## 11. Audit gates
- Approval/denial matrix (items 1-3) + exactly-once (item 4).
- Path/message confinement (items 5-6) + closed surface (item 7).
- Journaling proof (item 8) + engine integration (item 9).
- Invariants: `knowledge.db` SHA + `CONTRACT_VERSION` untouched + default
  policy git still deny.

## 12. Commit checkpoints
- `7B git staging and commit tools` (after GO audit + operator commit).

## 13. Non-goals
push, force, amend, hooks changes, config writes, ref/branch manipulation,
rollback of commits, staging via `--all`, submodule/network operations.

## 14. Risks
- Duplicate commits from a retried `commit`: git itself makes commit atomic
  (empty/staged-only), and we never auto-retry; exactly-once is enforced by
  git's own semantics + a clean-tree guard (item 4).
- Broad staging: confined to explicit validated paths, no `--all`.

## 15. Definition of Done
- All test items 1-10 green; Layer 5/6/7A regressions green.
- Approval + journaling + confinement proofs green; production KB SHA
  unchanged.