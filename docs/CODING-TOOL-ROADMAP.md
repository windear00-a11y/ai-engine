# Coding-Tool Roadmap — Original 9-Layer Plan

> **Status:** Project documentation — ORIGINAL roadmap preservation. No implementation code changed in this commit. This document records the original roadmap exactly as specified, plus the current checkpoint and next milestone derived strictly from the read-only audit. `Verified Edit Loop` is **not** an original layer/phase and is not inserted.

---

## Original Roadmap — 9 Layers

The original coding-tool roadmap (READ/ANALYSIS ONLY, not redesigned) consists of exactly these layers, in this order:

1. **Safety — Permission System**
2. **Project understanding — Persistent Project Indexer**
3. **File operations — patch application, atomic writes, snapshot + rollback, operation journal**
4. **Command execution — expand allowlist, resource/output limits**
5. **Verification — deterministic error parser + fix rules**
6. **Task orchestration — Planner + persistent task state**
7. **Git — gated status/diff/commit/rollback**
8. **Automation — indexing, lint-fix, format, changelog, regression**
9. **Optional AI boundary**

No additional layers have been added. No AI is assumed at any layer before Layer 9.

---

## Current Status (per latest read-only audit, HEAD `c62807a`)

| # | Layer | Status |
|---|---|---|
| 1 | Safety — Permission System | **complete** |
| 2 | Project understanding — Persistent Project Indexer | **complete** |
| 3 | File operations — patch application, atomic writes, snapshot + rollback, operation journal | **complete** |
| 4 | Command execution — expand allowlist, resource/output limits | **partial** |
| 5 | Verification — deterministic error parser + fix rules | **missing** |
| 6 | Task orchestration — Planner + persistent task state | **partial** (Planner complete, persistent task state missing) |
| 7 | Git — gated status/diff/commit/rollback | **missing** |
| 8 | Automation — indexing, lint-fix, format, changelog, regression | **missing** |
| 9 | Optional AI boundary | **optional/future boundary** |

Details per audit:

- **1 Safety — complete:** `tools/permissions` + `tools/coding/fs.py:16` + `ApprovalGate` + `hard_write_guard` + `audit` + `journal` + `rollback` — fully tested (72 permission + 19 rollback tests).
- **2 Project understanding — complete:** `tools/indexer` (`types`, `discovery`, `python_ast`, `store`, `query`, `incremental_update`) + deterministic `stable_id` + `IndexQueries` read-only — fully tested (49 tests).
- **3 File operations — complete:** `WriteTools.write/edit/diff/mkdir` + atomic + `snapshot_provider` + `EngineState` journal + `RollbackExecutor` (`tools/coding/write_tools.py:23`, `tools/permissions/journal.py:97`) — fully tested.
- **4 Command execution — partial:** `ExecutionRunner` allowlist currently `python` only (`tools/coding/exec_tools.py:75`) via `run_checked`; `timeout` + output caps present (`tools/coding/exec_tools.py:56`), `resource limits` (memory/process) not yet.
- **5 Verification — missing:** Only `project_check` `python_syntax` (`tools/coding/exec_tools.py:378`) exists; no deterministic `exit-code/stack-trace/test-output` parsers or fix-rule loop (`edit→run→inspect→map→fix→retest`).
- **6 Task orchestration — partial:** `DeterministicPlanner` (`tools/planner/deterministic.py:30`) + `planner.generate` registry (`engine/task_engine.py:103`) complete (26 tests); persistent `task` table `planned→rolled_back` history not yet (only `journal`/`audit` + in-memory `TaskEngine` result).
- **7 Git — missing:** `Domain.GIT` + `REASON_GIT_DENIED` remain deny (`tools/permissions/*`), no `git` tool implementation.
- **8 Automation — missing:** Beyond `indexing` (`discovery`), no `lint-fix/format/changelog/regression` tools.
- **9 Optional AI boundary — optional/future boundary:** Deterministic-first principle (`tools/planner/deterministic.py:3`) enforced; no AI/LLM/embeddings.

**Important distinction:** Layer 5 Verification *describes* the deterministic `edit→run→inspect→map→fix→retest` loop, but the term `Verified Edit Loop` is a **derived audit recommendation**, not original terminology, and must not be inserted as a new phase/layer. Keep original terminology.

---

## Current Checkpoint

- **Phase 1 A/B/C complete:** `A` Permission/Safety (`0f90a67` + `44d00c6`), `B` Persistent Project/Code Index (`c9b2518` + `2237595` incremental), `C` Deterministic Planner (`c62807a`).
- **Latest commit:** `c62807a Add deterministic planner` (HEAD, 6 ahead of `origin/master`).
- **Working tree currently clean:** `git status --porcelain` empty (verified pre- and post-audit).
- **Production `database/knowledge.db` untouched:** `sha256 000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91`, `integrity_check ok`, `foreign_key_check []`, `nodes 4846`, `relationships 1338`, `sources 6`.
- **Contract v1 frozen:** `api/contract.py:85` `CONTRACT_VERSION = "1"`, `git diff HEAD -- api/contract.py` empty.

---

## Next Milestone

**Layer 4 — Command Execution completion.**

Per the read-only audit and original roadmap priority `1 → 9`, Layer 4 is the only partially implemented layer that unblocks Layer 5 and subsequent layers. It is the smallest sensible next milestone (one layer, no new DB schema, additive to `tools/coding/exec_tools.py:75`).

**Remaining scope for Layer 4 (original/audited only):**

- Expand safe allowlist beyond current Python capability
- `lint` / `format` / `package-manager` / `project-script` support as justified
- `timeout` / `output limits` (already partially present via `run_checked`, preserve)
- Resource limits including `memory` / `process limits`
- Preserve existing permission/approval boundary (`ApprovalGate`, `PathPolicy`, `hard_write_guard`)
- **No AI**
- **No Git** (Layer 7)
- **No Verification implementation yet** (Layer 5 — deterministic error parser + fix rules)

Do not invent additional roadmap layers. Do not start Layer 4 implementation in this documentation commit. Do not add AI/LLM, Web App, or Git/network/publish functionality.

---

*This document is the source of truth for the ORIGINAL 9-layer coding-tool roadmap. Future audits must compare against this exact list.* 
