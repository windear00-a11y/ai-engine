# Slice 7A — Git status/diff/log inspection tools — implementation plan

Slice-format plan per `docs/MASTER-IMPLEMENTATION-PLAN.md` §7 and Section 7A.

## 1. Purpose
Deterministic, workspace-bounded, READ-ONLY Git inspection as first-class
engine tools (`git.status`, `git.diff`, `git.log`). No arbitrary shell, a
strict subcommand/arg allowlist, and results that never mutate the repo or
allow escaping the workspace.

## 2. Current state
- No git tooling exists. `policy.py` sets `git: deny`; `ApprovalGate` denies
  `Domain.GIT` outright.
- Engine tool registry (`TaskEngine._build_registry`) has no git.* entries.
- This repo's own git workflow (this controller) is manual; 7A gives the
  engine sanctioned read access.

## 3. Gap being closed
Dedicated read tools that parse `git status --porcelain`, `git diff
--no-ext-diff`, `git log --oneline -n <N>` through a closed allowlist with
workspace confinement — replacing "run git by hand" with a deterministic,
auditable surface (Layer 8 feeds on this).

## 4. Architecture
New package `tools/git/` — `GitTools(workspace_root)`:

- **Close-form allowlist** (subprocess-safe, no shell):
    - `status()`            -> `git status --porcelain`       (workspace root)
    - `diff(path=None)`     -> `git diff --no-ext-diff [-- REL]`
    - `log(n=20)`           -> `git log --oneline -n N`       (N clamped 1..200)
- **Confinement:** every invocation runs with cwd forced to
  `workspace_root` via `git -C <abs root>`; `diff` accepts at most ONE
  optional workspace-relative path, validated (reject absolute paths, `..`
  traversal, empty/`.` escapes) before being passed as `-- REL`.
- **Parsing:** `status --porcelain` lines parsed defensively into
  `{untracked, staged, modified, deleted, renamed, raw}` (deterministic
  ordering); `diff` -> `{files_changed, raw}`; `log` -> `{commits:[...]}`.
- **Fail closed:** missing repo / detached state / bad args return
  `{"ok": False, "error": ...}` — never raise, never run a different
  subcommand. Unknown/extra args are rejected before any process spawn.
- Environment is sanitized for reads (`GIT_CONFIG_NOSYSTEM=1`); no config
  mutation, no hooks, no push.
- Read tools do NOT require approval (equivalent to `file.read` /
  `knowledge.get`); `Domain.GIT` stays deny-by-default for WRITES (7B).

Registration: `TaskEngine.__init__` builds `self.git =
GitTools(workspace_root)`; `_build_registry()` adds `git.status`,
`git.diff`, `git.log`.

## 5. Dependencies
Layer 6 coordinator/platform (tool registry + persistent step results);
`git` binary present on PATH (this repo already relies on it).

## 6. Scope / files
- **New:** `tools/git/__init__.py` (`GitTools`), `tools/git/_core.py` if
  split preferred (single module acceptable).
- **Edit:** `engine/task_engine.py` — construct `self.git`, register the
  three tools.
- **New:** `tests/test_git_tools.py`.
- **No changes** to `tools/permissions/*`, `api/*`, `database/*`,
  `docs/CODING-TOOL-ROADMAP.md`, policy/GIT deny state.

## 7. Tests (test matrix)
1. Fixture repo (temp) with committed + modified + untracked files:
   `git.status()` returns all three classes with deterministic raw lines.
2. Empty/clean repo -> status with no entries (ok True).
3. `git.diff()` after a modification returns exactly that file; no repo at
   `workspace_root` -> `ok False` ("not a git repo"), never raises.
4. `git.diff(rel)` works for a valid relative path; absolute path,
   `../escape`, and outside-root paths are rejected (ok False) with NO
   subprocess spawned (arg validation precedes exec).
5. `git.log()` returns deterministic commit list (hash prefix + subject);
   `git.log(n=3)` clamps; `n=0`/negative/non-int rejected.
6. Read-only proof: repo bytes for `.git` key files + working tree unchanged
   after status/diff/log; `git rev-parse HEAD` constant.
7. Unknown subcommand / unexpected kwargs rejected before spawn.
8. Invariant: production `knowledge.db` SHA unchanged.
9. Engine integration: registry contains the three git tools; a coordinator
   task using `git.status` persists the step success + result.

## 8. Security checklist
- Closed allowlist; no shell=True; args validated pre-spawn; cwd confined.
- Reads only; no `add/commit/push/amend/config/hooks`; push never reachable.
- Paths bounded to workspace (containment check on resolved paths).
- `Domain.GIT` write-denial unchanged.

## 9. Persistence
Task step results only (coordinator transcript). Never into the repo's
objects/index/config and never into the production `knowledge.db`.

## 10. Failure / recovery
Missing repo/HEAD, malformed porcelain, unexpected git exit codes: returned
as `ok False` descriptions, no exception, no partial state. Deterministic
repeatable reads (no locks introduced).

## 11. Audit gates
- Arg-validation matrix (absolute/traversal/non-int/no-subprocess-spawn).
- Workspace confinement + read-only proof (rev-parse constant).
- Invariants: `knowledge.db` SHA + `CONTRACT_VERSION` untouched.
- Tool registry diff is exactly the three git.* registrations.

## 12. Commit checkpoints
- `7A git inspection tools` (after GO audit + operator commit).

## 13. Non-goals
Commits, staging, push, amend, hooks, config writes, ref manipulation,
non-workspace repos.

## 14. Risks
- porcelain/version drift: parsed defensively (raw preserved on every
  response; unknown XY codes grouped under raw without killing the read).
- Path confusion: resolved-prefix containment + reject traversal pre-spawn.

## 15. Definition of Done
- All test items 1-9 green; Layer 6 platform regressions green.
- Read-only + confinement proofs green; production KB SHA unchanged.