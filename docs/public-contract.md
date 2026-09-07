# Persistent Intelligence System — Public Contract (canonical)

> **Status:** authoritative description of the ACTUAL public interface.
> **Date:** 2026
> This document is implementation-backed. Anything listed under **Frozen** is
> code-enforced by the test suite. Nothing unimplemented is documented as
> implemented.

---

## 1. Frozen (public, stable)

### 1.1 Contract v1 (`api/contract.py`)

* `CONTRACT_VERSION = "1"` — frozen, read-only, deterministic.
* Operations: `search`, `get`, `related`, `follow`, `provenance`, `inspect`.
* Detailed spec: `docs/public-api-v1.md` (authoritative for v1).
* v1 must never change semantically; a necessary v1 change is reported, not
  silently applied (enforced by `tests/test_contract_v1.py` +
  `tests/test_phase21_contract_api.py`).

### 1.2 Contract v2 (`api/contract_v2.py`)

* `CONTRACT_VERSION = "2"` — additive generic Memory contract.
* Operations (inventory, exactly seventeen):

| Operation | Required args | Optional args |
| --- | --- | --- |
| `remember` | `payload` (object) | `context_hints` (object), `project_id` |
| `recall` | `query` (non-empty string) | `limit` (≤100, default 20), `candidate_limit`, `context` (object), `project_id`, `vocabulary_id` |
| `get` | `node_id` | `project_id`, `vocabulary_id` |
| `provenance` | `node_id` | `project_id`, `vocabulary_id` |
| `inspect` | — | `project_id`, `vocabulary_id` |
| `context.get` | `context_id` | `project_id` |
| `lifecycle.ingest` | `content` (non-empty string) | `origin`, `source`, `uri`, `role`, `actor`, `project_id`, `context_hints` (object) |
| `lifecycle.experience` | `situation`, `attempt`, `result` (strings) | `context_id`, `evidence_ids` (list), `task_id`, `outcome_classification`, `outcome_id`, `task_type`, `domain`, `strategy_id`, `actor`, `source`, `project_id` |
| `lifecycle.learning` | `experience_ids` (list) | `project_id` |
| `lifecycle.strategy` | `experience_ids` (list) | `project_id`, `min_samples` (positive int) |
| `lifecycle.trace` | `record_id` | `role`, `project_id`, `max_depth` (positive int) |
| `lifecycle.describe` | `record_id` | `role`, `project_id` |
| `lifecycle.summary` | — | `project_id` |
| `lifecycle.plan` | `situation` (non-empty string), `experience_ids` (list) | `context_id`, `constraints` (object), `max_steps` (positive int), `min_samples` (positive int), `project_id` |
| `lifecycle.grant` | `plan_id`, `plan_step_ids` (non-empty list), `actor` (non-empty string) | `mechanism`, `evidence_ids` (list), `project_id` |
| `lifecycle.authorize` | `plan_id`, `plan_step_ids` (non-empty list), `actor` (non-empty string) | `policy` (object), `request_ref`, `project_id` |
| `lifecycle.execute` | `plan_id`, `plan_step_id`, `actor` (non-empty strings) | `request_id`, `policy` (object), `executors` (object), `project_id` |

* `project_id` matches `^[a-z0-9_-]{1,64}$`; default project is `"default"`.
* `vocabulary_id` is accepted on read ops for capture-consistency but does not
  scope reads (reads are project-wide); an id that does not resolve to a
  loadable vocabulary is rejected with `invalid_argument`.
* Success shapes:
  * `remember` → `{ok, node_id, node_ids, activity_id, context_id, evidence_id}`
  * `recall` → `{query, knowledge: [...]}` (knowledge entries include
    `provenance` and `_score` when ranked)
  * `get` → one node object
  * `provenance` → provenance record (`node_id`, `source_id`, `source_name`,
    `source_version`, `source_location`, `imported_at`, and evidence fields)
  * `inspect` → `{project_id, node_count, context_count, evidence_count,
    activity_count}`
  * `context.get` → one context snapshot
  * `lifecycle.ingest` → `{record_id, role, origin, lifecycle_state, ...}`
    (`role` is `memory` for user facts or `knowledge` with `origin` `external`
     for external info, which is advisory and grounded to its source)
  * `lifecycle.experience` → `{experience_id, origin, outcome_id,
    outcome_classification, context_id, evidence_ids, lifecycle_state}`
  * `lifecycle.learning` → `{learning_id, experience_ids, counts,
    preserves_source}`
  * `lifecycle.strategy` → `{learning_id, strategy_count, strategies}` where
    each strategy carries `supporting_experience_ids` and
    `supporting_evidence_ids`
  * `lifecycle.trace` → `{root, records, meta}` — deterministic provenance walk
    over the lifecycle (strategy → learning → experience → outcome → evidence →
    source/context), always cycle-safe and depth-bounded
  * `lifecycle.describe` → one record with full lifecycle provenance
  * `lifecycle.summary` → `{project_id, total_records, by_role, by_origin,
    by_state}`
  * `lifecycle.plan` → the full intelligence chain in one deterministic pass
    (Experience → Learning → Strategy → Strategy Application → Reasoning →
    Decision → Plan), returning
    `{learning_id, strategy_count, strategies, strategy_application_id,
    strategy_application_status, reasoning_id, reasoning_status, decision_id,
    decision_status, plan_id, plan_status, action_handoff}`. Ends at the plan
    boundary — no action is executed. All records carry `derived_from`
    provenance linking plan → decision → reasoning → strategy application →
    strategy. Deterministic across repeated calls with the same project.
  * `lifecycle.grant` → `{grant_id, plan_id, plan_step_ids, actor, mechanism,
    state: "approved", project_id, role: "authorization"}` — explicit, audited
    approval (idempotent; duplicate grants return `duplicate: true`).
  * `lifecycle.authorize` → `{authority_id, plan_id, plan_step_ids, actor,
    decision, per_step, project_id, role: "authority"}` — deterministic
    authority evaluation that persists an `authority` record. `decision` is one
    of `approved`, `denied`, `requires_approval`, `invalid_plan`,
    `invalid_step`, or `unknown`; `unknown` is never approval and never
    permits execution.
  * `lifecycle.execute` → the safe `action` attempt record
    (`{action_id, plan_id, plan_step_id, actor, authority_decision,
    execution_status, registered_effect, observation_ids, ...}`). Authority is
    evaluated first: anything other than `approved` produces a `denied` action
    that never reaches the effect layer.
* `remember` records the full pipeline in one commit:
  *remember → activity → context → knowledge node → evidence*. Outcome /
  experience / strategy are produced by the (internal) intelligence layer, not
  by the Memory contract.
* The lifecycle contract (`lifecycle.*`) implements the single canonical
  lifecycle grammar: INFORMATION → SOURCE/ORIGIN → SOURCE RECORD → STRUCTURED
  MEMORY → KNOWLEDGE and/or EXPERIENCE → LEARNING → STRATEGY → REASONING/
  DECISION → ACTION → OUTCOME → EVIDENCE → EXPERIENCE. Records carry first-class
  provenance (record_id, origin, source, actor, timestamp, project,
  context_id, evidence_ids, confidence, lifecycle_state, parent_record_ids,
  derived_from). Trust boundary: EXTERNAL information is advisory knowledge,
  never an experience or strategy on its own (enforced and audited).

### 1.3 Memory API (Python, `api/memory_api.py`)

* `MemoryAPI(data_root=None, default_vocabulary="diary_v1")` — per-project,
  deterministic; never touches v1.
* Methods: `remember(payload, context_hints=None, project_id=None,
  vocabulary_id=None)`, `recall(query, limit=None, candidate_limit=None,
  context=None, project_id=None, vocabulary_id=None)`,
  `get(node_id, project_id=None, vocabulary_id=None)`,
  `provenance(node_id, project_id=None, vocabulary_id=None)`,
  `inspect(project_id=None, vocabulary_id=None)`,
  `context_get(context_id, project_id=None)`.
* Lifecycle methods (Phase 26, additive): `lifecycle_ingest(content,
  origin=None, source=None, uri=None, role=None, actor=None, project_id=None,
  context_hints=None)`, `lifecycle_experience(situation, attempt, result,
  ...)`, `lifecycle_learning(experience_ids, project_id=None)`,
  `lifecycle_strategy(experience_ids, project_id=None, min_samples=None)`,
  `lifecycle_trace(record_id, role=None, project_id=None, max_depth=None)`,
  `lifecycle_describe(record_id, role=None, project_id=None)`,
  `lifecycle_summary(project_id=None)`.
* Lifecycle planning (Phase 28, additive): `lifecycle_plan(situation,
  experience_ids, context_id=None, constraints=None, max_steps=None,
  min_samples=None, project_id=None)` — runs the deterministic intelligence
  chain; nothing is executed.
* Vocabulary errors (unknown vocabulary id) are raised as
  `KnowledgeArgumentError` → `invalid_argument`, not `internal_error`.

### 1.4 MemoryTools (`api/memory_tools.py`) and memory session

* `MemoryToolInterface(data_root=None)` — transport-independent tool-call
  interface implementing v2 (envelope + error codes).
* Shared contract semantics with `MemoryAPI`; identical fields pass through
  (`remember`, `recall`, `get`, `provenance`, `inspect`, `context.get`).
* Persistent session: `api/memory_session.py` (`MemorySessionServer`,
  JSON-lines framing, one shared `MemoryToolInterface`).

### 1.5 SDK — `MemoryClient` (`knowledge_client/memory_client.py`)

* Methods mirror contract v2 exactly: `remember(payload, context_hints=None,
  project_id=None)`, `recall(query, limit=20, project_id=None, context=None,
  candidate_limit=None, vocabulary_id=None)`, `get(node_id, project_id=None,
  vocabulary_id=None)`, `provenance(node_id, project_id=None,
  vocabulary_id=None)`, `inspect(project_id=None, vocabulary_id=None)`,
  `context_get(context_id, project_id=None)`.
* Lifecycle planning (Phase 28, additive): `plan(situation, experience_ids,
  context_id=None, constraints=None, max_steps=None, min_samples=None,
  project_id=None)` issuing the `lifecycle.plan` operation.
* Transports (`knowledge_client/transports.py`):
  * `MemoryInProcessTransport(data_root=None, interface=None)` — direct call.
  * `MemorySessionTransport(data_root=None, ...)` — persistent subprocess session.
  * `HttpMemoryTransport(host, port, api_key=None)` — HTTP `/v2/execute`.
* No transport may silently invent or drop fields (enforced by tests).

### 1.6 HTTP (`http_server/server.py`)

* `POST /v1/execute` → v1 envelope.
* `POST /v2/execute` → v2 envelope (`ok`, `operation`, `contract_version`,
  `result` / `error.code`).
* `GET /health` → `{ok: true, ...}`.
* Optional API-key gate for all `/execute` endpoints.
* HTTP status mapping: `invalid_request`/`invalid_argument`/`invalid_relationship_type`/`unknown_operation` → 4xx; `internal_error` → 500; unauthorized → 401.

### 1.7 Stable error codes (shared v1/v2)

| Code | Meaning |
| --- | --- |
| `invalid_request` | request not a JSON object / malformed structure |
| `unknown_operation` | unknown `operation` |
| `invalid_argument` | missing/wrong/out-of-range argument (incl. bad `project_id`, bad `vocabulary_id`, malformed `payload`, bad limits) |
| `invalid_relationship_type` | non-canonical relationship kind (v1 `follow`) |
| `node_not_found` | unknown node **or** unknown context id (stable code reused) |
| `internal_error` | unexpected failure behind the boundary (no details leaked) |

Error envelopes never contain stack traces or implementation details.

### 1.8 Isolation, identity, provenance, determinism

* **Project isolation:** each project has its own database set created via
  `ai_engine.paths`; a node id from project A never resolves in project B
  (`node_not_found`); explicit `project_id` is honored everywhere; omitting it
  deterministically uses `"default"`.
* **Deterministic IDs:** node/activity/context/evidence ids are content-derived;
  repeating the identical `remember` payload yields the identical `node_id`.
* **Provenance:** never invented — only what exists (`manual://` sources for
  CLI/manual capture).
* **Read-only ops:** all six v1 ops and the non-`remember` v2 ops never write;
  enforced via database-hash checks in tests.
* **No smuggling:** `sql`, `path`, `command` argument names are rejected by the
  strict per-operation whitelist for every operation in v1 and v2.

---

## 2. Internal (implementation, never part of the public contract)

* Persistent-intelligence internals: `ai_engine/lifecycle.py`,
  `ai_engine/generic_planner.py`, `ai_engine/generic_learning.py`,
  `ai_engine/registry.py`, `ai_engine/runtime.py`,
  `ai_engine/persistence.py`, `ai_engine/migration.py`, strategy modules.
* Capture pipeline internals: `ai_engine/capture.py`, `ai_engine/activity.py`,
  `ai_engine/rank.py` / `ai_engine/ranker.py`.
* v1 engine internals: `retrieval.*`, `knowledge_repository`,
  `knowledge_compiler`, `acceptance`, `api/session.py` line framing.
* Intelligence inspector API (`intelligence/api/contract.py` — separated from
  Contract v1/v2): `experience.search`, `experience.get`, `decision.get`,
  `decision.audit`, `learning.events`, `knowledge.confidence`,
  `knowledge.lifecycle`, `context.get`, `context.similar`. Read-only,
  inspected via `intelligence/api/handler.py`. Not a public Memory contract.
* Persistence / trust fabric / verification internals that back the frozen
  guarantees above.

## 3. Optional domain plugin: Code

* `ai_engine/plugins/code.py` — the single coding-specific boundary.
  Registers its tool set explicitly via `register_code_domain_tools`;
  `is_coding_task`/`is_coding_module`/`is_generic_task` classify scope.
* `CODE_SPECIFIC_MODULES` are never part of the generic core: `tools/coding`,
  `tools/indexer`, `tools/planner/deterministic.py`, `tools/verification`,
  `tools/git`, `knowledge_compiler`, `sources/cpython`.
* Generic core (including `ai_engine.memory`, `api.memory_api`, Contract v1/v2,
  `MemoryClient`, HTTP, CLI) imports and runs **without** the Code plugin;
  the Code domain is explicitly registered and never auto-loaded.
* Code vocabulary `code_v1` (`ai_engine/vocabularies/code_v1.json`); the
  generic privacy/security invariants apply regardless of plugin presence.
* v1 and v2 contain no coding-specific operations.