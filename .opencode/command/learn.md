---
description: Run the controlled /learn workflow — sync the Learning Mirror, prepare AI proposals, review, WAIT for your approval, then apply+commit. /learn status | /learn prepare | /learn approve.
agent: build
---

# /learn — controlled AI learning update workflow

Orchestrate the existing **Phase 3 (sync.py)** + **Phase 4 (author.py)** tooling to
safely update the Learning Mirror for this project (`/root/ai-engine`). You do NOT
reimplement their logic — you drive a thin facade and insert the human-review gate.

## Context
- Source project (READ-ONLY to the mirror workflow): `/root/ai-engine` (this repo).
- Learning Mirror (separate git repo): `/mnt/sdcard/Documents/markor/learning/ai-engine`
- Facade: `<mirror>/_meta/tools/learn.py` with subcommands `status | prepare | approve [--yes]`.
- The facade resolves source/mirror itself; always pass `--source /root/ai-engine
  --mirror /mnt/sdcard/Documents/markor/learning/ai-engine`.

## Safety invariants (non-negotiable)
- NEVER modify `/root/ai-engine` (the source) — it is READ-ONLY to this workflow.
- NEVER modify or commit `## Personal Notes` or `_meta/learning/*` — they are read-only.
- NEVER commit the transient `_meta/state/review/` bundle (it is scratch staging).
- The Learning Mirror must never contain source-code bodies or secrets; the guard
  enforces this — never try to bypass it.
- `/learn` must NOT silently modify/commit learning docs. The human must always
  review and explicitly approve before anything applies.

## Steps

### `$ARGUMENTS` handling
Arguments are optional. Supported:
- `/learn`            → full flow (detect → prepare → review → WAIT)
- `/learn status`     → only report mirror sync state, modify NOTHING
- `/learn prepare`    → detect + sync + generate proposals (stop at review)
- `/learn approve`    → apply the prepared review + commit (honors the --yes gate)

### Full flow (default)
1. **Detect** — run:
   `python3 /mnt/sdcard/Documents/markor/learning/ai-engine/_meta/tools/learn.py --source /root/ai-engine --mirror /mnt/sdcard/Documents/markor/learning/ai-engine status`
   - `LEARN_STATUS=UP_TO_DATE` → report "mirror already up to date (HEAD <x>)" and STOP.
   - `LEARN_STATUS=SOURCE_DIRTY` → tell the user to commit `/root/ai-engine` first, STOP.
   - `LEARN_STATUS=SYNC_REQUIRED` → continue.

2. **Prepare** — when sync is required, run:
   `python3 /mnt/sdcard/Documents/markor/learning/ai-engine/_meta/tools/learn.py ... prepare`
   This runs `sync --yes` (deterministic facts + worklist + sync commit) then
   `author --prepare --stub` and prints the review bundle location + a summary.
   The mirror path defaults are baked in; use the full command above.

3. **Show review & WAIT** — read and PRESENT to the user:
   - `<mirror>/_meta/state/review/manifest.md` (what changed, source commits)
   - `<mirror>/_meta/state/review/proposals.json` (the prepared proposals to apply)
   - `<mirror>/_meta/state/review/context.md` (diff stats — NEVER dump bodies)
   Show each affected doc and the proposed Current-Understanding / Evolution
   changes. Then STOP and ask the user for approval via the `question` tool with
   options `approve` (apply+commit) / `reject` (leave mirror unchanged) /
   `edit` (let the user rewrite proposals.json first). DO NOT apply anything yet.

4. **Approve** — ONLY after explicit user approval, run:
   `python3 /mnt/sdcard/Documents/markor/learning/ai-engine/_meta/tools/learn.py ... approve --yes`
   `--yes` is the explicit Phase 4 approval gate; the apply validates invariants,
   runs guard + scaffold `--verify`, confirms `/root/ai-engine` is still pristine,
   then commits. Report `APPROVE=APPLIED` and the mirror commit hash.
   - If the user REJECTS or wants edits: do NOT run `--yes`. With `edit`, let them
     rewrite `<mirror>/_meta/state/review/proposals.json`, then re-run `approve --yes`.

5. **Verify & report** — after applying, confirm:
   - `git -C <mirror> log --oneline -1` shows the new commit.
   - `git -C /root/ai-engine status --porcelain` is empty (source pristine).
   - Safety battery green (`<mirror>/_meta/tools/guard.py --self-test`).

## Recommendation on triggers
- Prefer invoking `/learn` explicitly (as above).
- Optional `[learn]` commit-marker convention: if you notice a source commit message
  contains `[learn]`, you MAY ask the user "want to run /learn for this change?"
  — but NEVER auto-run. `@learn` inline is redundant with the slash command; not
  recommended.
- Do NOT set up background watching or auto-run `/learn` per source change.

## Notes
- Idempotent: repeated `/learn` when nothing changed is a no-op.
- If `learn.py approve` reports `APPROVE=NOTHING_TO_APPLY`, there is nothing pending —
  report that.
