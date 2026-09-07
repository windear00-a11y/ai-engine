# Universal Data Ingestion v1 — Milestone Proposal

> **Status:** COMPLETE — GO (all 443 tests pass, production DB safe)
> **Date:** 2026-08-26
> **Scope:** ai-engine only (Knowledge Engine)
> **Contract v1:** Frozen — no changes
> **Production DB:** SAFE (SHA-256: `000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91`)

---

> **Errata (addendum, historical record):** this milestone's "PRAGMA foreign
> keys OFF at runtime" claims (lines 29, 314, 328, 868) are **incorrect**.
> `retrieval/repository.py` enables `PRAGMA foreign_keys=ON` on every
> `KnowledgeRepository` connection (verified by `docs/SAFETY-AUDIT-phase0.5.md`
> and `tests/test_sqlite_repository.py`). The milestone describes planned work;
> the shipped implementation enforces foreign keys.

---

## A. Already Implemented

### A1. Core Data Model (Repository Layer)
**File:** `retrieval/repository.py`

| Feature | Status | Notes |
|---------|--------|-------|
| SQLite schema (`sources`, `nodes`, `relationships`) | **Complete** | Generic, domain-agnostic |
| `sources.id` (autoincrement PK) | **Complete** | |
| `nodes.id` (TEXT PK) | **Complete** | Arbitrary string IDs |
| `nodes.type` (TEXT NOT NULL) | **Complete** | Free-form; validated at app layer |
| `nodes.name`, `nodes.description` | **Complete** | |
| `nodes.metadata` (JSON TEXT) | **Complete** | Arbitrary key-value extras stored here |
| `relationships` table | **Complete** | `source_node_id` FK (CASCADE), `target_node_id` intentionally NOT FK (forward refs OK) |
| `UNIQUE(source_node_id, relationship_type, target_node_id)` | **Complete** | |
| Indexes: `idx_nodes_type`, `idx_rel_source`, `idx_rel_target` | **Complete** | |
| `import_source()` (atomic bulk insert) | **Complete** | One transaction per source |
| `import_node()` (atomic single node insert) | **Complete** | |
| `PRAGMA foreign_keys=ON` | **Complete** | But note: current production DB has FK=OFF at runtime |

### A2. Ingestion Pipeline (Source File → SQLite)
**Files:** `ingestion/`

| Feature | Status | Notes |
|---------|--------|-------|
| Canonical source format (v1.0) | **Complete** | `source_format.py` |
| Source validation (structure, types, referential integrity) | **Complete** | `validator.py` — structured `ValidationError` with code/message/path |
| Atomic import (single source → SQLite transaction) | **Complete** | `importer.py` |
| CLI: `python -m ingestion validate/import/inspect` | **Complete** | `__main__.py` |
| Deterministic, no-AI, no-network | **Complete** | |

### A3. Safe Import Dry-Run Layer
**Files:** `importing/`

| Feature | Status | Notes |
|---------|--------|-------|
| Import plan loader | **Complete** | `plan_loader.py` |
| Static plan verification (structure, types, PK uniqueness, FK, provenance, unsafe paths, read-only guard) | **Complete** | `verifier.py` — 320 lines |
| Dry-run simulation (`:memory:` SQLite) | **Complete** | `dry_run.py` — proves compatibility |
| Controlled production import (backup → atomic → post-verify) | **Complete** | `importer.py` — 456 lines |
| CLI: `python -m importing verify/dry-run/import` | **Complete** | `__main__.py` |
| Read-only guarantees for verify/dry-run | **Complete** | Verified by test suite with DB hash checks |

### A4. Acceptance Layer
**Files:** `acceptance/`

| Feature | Status | Notes |
|---------|--------|-------|
| Candidate evaluation (ACCEPT/HOLD/REJECT) | **Complete** | |
| Import plan generation (`prepare` command) | **Complete** | Writes `import_plan.json` |
| SQLite write-safety guard (`SQLITE_WRITES_FORBIDDEN`) | **Complete** | `safety.py` |
| Identity conflict detection | **Complete** | |

### A5. Knowledge Compiler
**Files:** `knowledge_compiler/`

| Feature | Status | Notes |
|---------|--------|-------|
| Source extraction → candidates | **Complete** | CPython source adapter |
| Candidate validation (provenance grounding) | **Complete** | `validator.py` |
| Backfill rules (inferred relationships) | **Complete** | `backfill.py` |

### A6. Contract v1 (Read Operations)
**Files:** `api/`, `docs/public-api-v1.md`

| Operation | Status | Notes |
|-----------|--------|-------|
| `search` | **Complete** | Full-text, scored, deterministic |
| `get` | **Complete** | Single node by ID |
| `related` | **Complete** | Neighbours (incoming + outgoing) |
| `follow` | **Complete** | Relationship traversal |
| `provenance` | **Complete** | Source/provenance for a node |
| `inspect` | **Complete** | Aggregate statistics |

### A7. Transport Layer
| Transport | Status | Notes |
|-----------|--------|-------|
| In-process (`ToolInterface`) | **Complete** | |
| Persistent session (stdio) | **Complete** | `api/session.py` |
| HTTP server (stdlib) | **Complete** | `http_server/server.py` |
| Python SDK (`KnowledgeClient`) | **Complete** | 3 transports |
| External HTTP client | **Complete** | `external_http_client/` |
| Web app (read-only SPA) | **Complete** | `webapp/index.html` |

### A8. Test Coverage
| Test File | Status |
|-----------|--------|
| `test_ingestion.py` | **33 tests pass** |
| `test_importing.py` | **Complete** |
| `test_importer.py` | **Complete** |
| `test_acceptance.py` | **Complete** |
| `test_contract_v1.py` | **Complete** |
| `test_api.py` | **Complete** |
| `test_http_server.py` | **Complete** |
| `test_knowledge_client.py` | **Complete** |
| `test_webapp.py` | **Complete** |

---

## B. Missing (For Universal Data Ingestion v1)

### B.1. Universal Data Ingestion v1 — COMPLETED (Phase 6M)

**Final test results:** 443 passed, 6 skipped, 0 failed
**Decision:** GO — all gates passed

| Gate | Status |
|------|--------|
| All 443 tests pass | PASS |
| Production DB byte-identical | PASS |
| Security hardened | PASS |
| No performance regression | PASS |
| Contract v1 fully compatible | PASS |
| Domain types extensible | PASS |
| External import pipeline safe | PASS |

**Phase completion summary:**
| Phase | Name | Status | Tests |
|-------|------|--------|-------|
| 0 | Baseline + Backup | COMPLETE | — |
| 0.5 | Safety Audit | COMPLETE | — |
| 1 | Universal Vocabulary | COMPLETE | 77 |
| 2 | External Validation + Dry-Run | COMPLETE | 81 |
| 3 | Staging → Approval → Apply | COMPLETE | 50 |
| 4 | Multi-Domain Acceptance | COMPLETE | 209 pass, 6 skip |
| 5 | Performance & Scalability | COMPLETE | 26 |
| 6A | Lazy Loading Investigation | COMPLETE | — |
| 6B | SQLite PRAGMA Optimization | COMPLETE | — |
| 6C | FTS5 Experiment | COMPLETE | REJECTED (semantic incompatible) |
| 6D | Concurrency Audit | COMPLETE | — |
| 6E | Full Universal Import E2E | COMPLETE | — |
| 6F | Web App Read Integration | COMPLETE | 13 endpoints |
| 6G | Security Review | COMPLETE | 5 checks |
| 6H | Failure/Rollback Testing | COMPLETE | 6 scenarios |
| 6I | Contract v1 Compatibility | COMPLETE | 10 operations |
| 6J | Performance Regression | COMPLETE | avg 1.96s search |
| 6K | Production DB Safety | COMPLETE | hash match |
| 6L | Full Test Suite | COMPLETE | 443/443 |
| 6M | Final Report | COMPLETE | — |

**Key findings:**
- Search bottleneck: Python-bound `_scan_scores()` full table scan (~2s)
- FTS5 117x faster but incompatible semantics (token vs substring) — rejected
- SQLite PRAGMA/WAL/mmap minimal search improvement (~200-275ms) — not implemented
- Lazy loading saves ~0.7s per API construction — not implemented (premature optimization)
- `_execute_lock` serializes all HTTP requests — safe but limits throughput
- 5 concurrent KnowledgeAPI instances: all succeed with 120s timeout

### B1. Node Type Vocabulary — Currently Closed
**Current:** 7 types: `concept`, `technology`, `entity`, `procedure`, `rule`, `example`, `dependency`  
**Missing:** Types for arbitrary domains:
- `person` — a named individual
- `company` — an organization
- `product` — a product or service
- `document` — a document, paper, article
- `event` — a happening or occurrence
- `research_paper` — a specific paper
- `location` — a geographic or virtual place

**Impact:** `VALID_TYPES` in `retrieval/knowledge.py:6-14` is a `set` used by the validator. External users cannot import data with types like `person` or `company` without extending this set.  
**Safe fix:** Add new types to `VALID_TYPES`. Additive, backward-compatible.

### B2. Relationship Type Vocabulary — Currently Closed
**Current:** 9 kinds: `depends_on`, `related_to`, `part_of`, `instance_of`, `implements`, `extends`, `uses`, `example_of`, `references`  
**Missing:** Types for arbitrary domains:
- `works_at` — person → company
- `founded` — person → company
- `authored` — person → document/research_paper
- `acquired` — company → company/product
- `manufactures` — company → product
- `cites` — document → document
- `subscribes_to` — person → product/service
- `located_in` — entity → location

**Impact:** `RELATIONSHIP_KINDS` in `retrieval/knowledge.py:16-26` is used by the verifier as warnings (not errors). The verifier already warns but doesn't reject unknown types (`verifier.py:207-211`).  
**Safe fix:** Add new relationship kinds. Additive, backward-compatible.

### B3. External User Import Flow (Direct Source → DB)
**Current:** The pipeline is: extraction → acceptance → import_plan.json → verify → dry-run → import. This is designed for the knowledge compiler's output.  
**Missing:** A simpler path for external users who already have structured data:
```
External JSON → validate → preview → approve → import
```
No acceptance layer needed — the user IS the authority.

### B4. Duplicate/Conflict Handling for Cross-Source Imports
**Current:** 
- `nodes.id` is a PRIMARY KEY — duplicate IDs are rejected (SQLite constraint)
- `importing/importer.py:307-313` rejects dangling relationship targets
- No merge/update strategy exists

**Missing:**
- Strategy for "same concept, different source" (e.g., two companies import "React")
- Node update/merge logic (new metadata, new relationships, keep existing)
- Source attribution in conflict reports

### B5. Source Identity / Namespacing
**Current:** Sources have `name` + `location` + `version` but no namespace or domain prefix.  
**Missing:** For multi-domain imports, sources need:
- Domain namespace (e.g., `tech:react`, `corp:acme-inc`)
- Collision detection across sources
- Source provenance queries (list all sources, nodes by source)

### B6. Batch/Multi-File Import
**Current:** `import_source_file()` handles one file at a time.  
**Missing:**
- Directory scan import (similar to `KnowledgeStore.load()` but with the full validation pipeline)
- Cross-file referential integrity checking
- Import manifest (list of files, pre-flight check)

### B7. Web App Import UI
**Current:** `webapp/index.html` is read-only.  
**Missing:**
- File upload endpoint
- Validate/preview endpoint
- Import/apply endpoint
- Import status/history endpoint
- UI components (upload form, validation errors, preview table, approval button)

### B8. Import CLI (Unified)
**Current:** Two separate CLIs:
- `python -m ingestion validate/import/inspect` (source file level)
- `python -m importing verify/dry-run/import` (import plan level)

**Missing:** A single unified CLI:
```
python -m ai_engine import data.json --dry-run
python -m ai_engine import data.json --preview
python -m ai_engine import data.json --apply
```

### B9. Staging Database
**Current:** Dry-run uses `:memory:`, production import writes directly to `database/knowledge.db`.  
**Missing:** A staging database concept — import to a temporary file-backed DB, then promote to production after approval.

---

## C. Safe to Reuse

| Component | Reuse Strategy |
|-----------|---------------|
| `retrieval/repository.py` (KnowledgeRepository) | **Direct reuse.** Schema is generic. All table structures support arbitrary node types and relationships. |
| `ingestion/source_format.py` | **Direct reuse.** `build_source()` / `build_node()` helpers work for any domain. |
| `ingestion/validator.py` | **Direct reuse with extension.** Validates structure/types; domain-specific rules can be added. |
| `ingestion/importer.py` | **Direct reuse.** Atomic import of source files into SQLite. |
| `importing/verifier.py` | **Direct reuse.** Static verification of import plans. |
| `importing/dry_run.py` | **Direct reuse.** Simulation in `:memory:` repository. |
| `importing/importer.py` | **Direct reuse.** Production import with backup + post-verify. |
| `importing/report.py` | **Direct reuse.** Report generation. |
| `acceptance/` | **Bypass for external imports.** External users provide validated data directly. |
| `api/contract.py` | **No changes needed.** Contract v1 is frozen; imported data is visible through existing operations. |
| `api/knowledge_api.py` | **No changes needed.** All 6 operations work on any node type. |
| `http_server/server.py` | **Extend only.** Add import endpoint alongside existing `/v1/execute`. |
| `webapp/index.html` | **Extend only.** Add import UI tab. |

---

## D. Needs Redesign

### D1. `VALID_TYPES` — From Closed Set to Open Set
**Current:** `VALID_TYPES` is a `set` of 7 types used by the validator and contract.  
**Redesign:** 
- Option A: Add new types to the existing set (simplest, additive)  
- Option B: Make types open-ended but validate structure only (no type check)  
- Option C: Configurable type registry with core + domain-specific types  

**Recommendation:** Option A for v1. The schema already stores types as free-form TEXT in SQLite. The `VALID_TYPES` check is a validation guard, not a storage constraint. Adding types is a one-line change.

### D2. `RELATIONSHIP_KINDS` — From Closed Set to Open Set
**Current:** Similar to `VALID_TYPES`.  
**Recommendation:** Option A — add new kinds. The verifier already warns (not errors) on unknown kinds.

### D3. Node Metadata — From Implicit to Explicit Schema
**Current:** Extra fields in source JSON are stored as `_extras` → `metadata` JSON blob. No schema.  
**Redesign:** For multi-domain support, consider:
- Optional domain-specific metadata schema validation
- Required metadata fields per node type (e.g., `person` requires `birth_date`?)
- Recommended metadata fields documentation

**Recommendation:** Keep implicit for v1. Document recommended metadata per domain.

---

## E. Recommended Next Milestone

### "Universal Data Ingestion v1"

**Goal:** Enable external users to import structured knowledge for arbitrary domains (person, company, product, document, event, concept, research_paper) through a validated, audited, reversible pipeline — without changing Contract v1 or the database schema.

### Phase Breakdown

#### Phase 0 — Baseline + Backup
**Goal:** Establish an immutable baseline before any changes.

**Files affected:** None (read-only)

**What changes:** Nothing. Record baseline state.

**What does NOT change:** Everything.

**Baseline measurements (current):**
```
DB SHA-256: 000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91
Foreign keys: OFF (PRAGMA at runtime)
Sources: 6
Nodes: 4846 (concept: 185, dependency: 136, entity: 1092, example: 3025, procedure: 81, rule: 2, technology: 325)
Relationships: 1338 (depends_on: 150, example_of: 1143, extends: 17, implements: 1, instance_of: 1, part_of: 11, references: 5, related_to: 9, uses: 1)
Integrity: ok
Dangling rel sources: 0
```

**Tests required:**
- Record baseline hash in a test constant
- `test_production_db_unchanged()` — SHA-256 before/after Phase 0

**Acceptance criteria:**
- Baseline hash recorded
- `PRAGMA foreign_keys` documented (currently 0 at connection time — this is a known issue)
- No database modifications

**DB safety:** Read-only. `mode=ro` connection.

**Rollback:** N/A (no changes made).

---

#### Phase 1 — Universal Import Format
**Goal:** Define the universal source format that supports arbitrary domains.

**Files affected:**
- `ingestion/source_format.py` — add `DOMAIN_TYPES` constant, document universal format
- `retrieval/knowledge.py` — extend `VALID_TYPES` with new types
- `knowledge/SCHEMA.md` — update documentation

**What changes:**
1. Extend `VALID_TYPES` with domain types:
   ```python
   VALID_TYPES = {
       # Core types (existing)
       "concept", "technology", "entity", "procedure", "rule", "example", "dependency",
       # Universal domain types (new)
       "person", "company", "product", "document", "event",
       "research_paper", "location",
   }
   ```
2. Extend `RELATIONSHIP_KINDS` with domain relationships:
   ```python
   RELATIONSHIP_KINDS = {
       # Existing 9 kinds...
       # New domain kinds
       "works_at", "founded", "authored", "acquired",
       "manufactures", "cites", "subscribes_to", "located_in",
   }
   ```
3. Document the universal source format in `SCHEMA.md`
4. Add a `universal` example source file

**What does NOT change:**
- SQLite schema (already supports arbitrary types via TEXT columns)
- Contract v1 (read operations work on any type)
- API layer (`api/contract.py`, `api/knowledge_api.py`)
- HTTP server routes
- Importing/verification layer (structure-based, not type-based)
- Production database

**Tests required:**
- `test_valid_types_includes_universal` — verify new types accepted
- `test_universal_source_format` — validate a person source
- `test_universal_source_import` — import and query a person node
- `test_existing_types_still_work` — backward compatibility
- `test_contract_v1_compatibility` — contract tests still pass

**Acceptance criteria:**
- A source file with `type: "person"` passes validation
- A source file with `type: "company"` passes validation
- All existing tests pass unchanged
- Contract v1 compatibility tests pass

**DB safety:** Tests run on `:memory:`. No production DB changes.

**Rollback:** Revert the `VALID_TYPES` / `RELATIONSHIP_KINDS` changes.

---

#### Phase 2 — Validation + Dry-Run
**Goal:** Strengthen validation for external user data and provide dry-run without the acceptance pipeline.

**Files affected:**
- `ingestion/validator.py` — add universal validation rules
- `ingestion/importer.py` — add `dry_run_file()` function
- `ingestion/__main__.py` — add `dry-run` subcommand
- `ingestion/source_format.py` — add required metadata per type

**What changes:**
1. Add type-specific validation rules:
   - `person`: require `name` (already required), recommend `birth_date`, `nationality`
   - `company`: require `name`, recommend `founded_date`, `industry`
   - `document`: require `name`, recommend `author`, `publication_date`
   - etc. (recommended, not enforced)
2. Add `dry_run_file()` — import a source file into `:memory:`, report projected state, discard
3. Add `python -m ingestion dry-run <file>` CLI command
4. Add source metadata requirements:
   - `source.name` required (already enforced)
   - `source.version` recommended for traceability
   - `source.submitter` optional (for external user attribution)

**What does NOT change:**
- SQLite schema
- Contract v1
- Importing/verification layer (still for acceptance pipeline)
- Production database

**Tests required:**
- `test_dry_run_does_not_touch_production` — SHA-256 check
- `test_dry_run_produces_correct_projection` — counts, types
- `test_dry_run_rollback_on_failure` — verify cleanup
- `test_universal_validation_rules` — per-type validation
- `test_dry_run_cli` — CLI output format

**Acceptance criteria:**
- `python -m ingestion dry-run person_source.json` produces validation report without touching production DB
- Dry-run is strictly read-only (verified by test)
- All existing tests pass

**DB safety:** Dry-run uses `:memory:` only.

**Rollback:** Revert new files and changes.

---

#### Phase 3 — Staging/Approval/Apply
**Status:** IMPLEMENTED
**Goal:** Implement a staging database concept for safe import workflow.

**Files affected:**
- New: `external_import/staging.py` — staging database creation (243 lines)
- New: `external_import/preview.py` — preview report generation (216 lines)
- New: `external_import/apply.py` — atomic apply with backup + post-verify (310 lines)
- Modified: `external_import/__main__.py` — added `stage`, `preview`, `apply` CLI commands
- New: `tests/test_phase3.py` — 50 comprehensive tests

**What was implemented:**
1. **Staging database:** A file-backed SQLite DB containing only NEW, non-conflicting items:
   - Created per import session via `create_staging(data, staging_path, prod_db_path)`
   - Reads production DB read-only to detect conflicts
   - Copies only new nodes/relationships into staging
   - Skips existing identical nodes (same type, name, description)
   - Excludes conflicting nodes (different content than production)
   - Tracks source creation/reuse deterministically

2. **CLI workflow:**
   ```
   python -m external_import validate  <file>                     # structural validation
   python -m external_import dry-run   <file>  [--db PROD_DB]     # conflict detection (read-only)
   python -m external_import stage     <file>  [--db PROD_DB] [--staging PATH]  # create staging DB
   python -m external_import preview   <staging_db> [--db PROD_DB]  # preview what apply would change
   python -m external_import apply     <staging_db> --db PROD_DB    # atomic apply with backup
   ```

3. **Preview report:**
   - Reads staging + production (both read-only)
   - Classifies staged nodes: new vs. already-in-production
   - Shows projected counts after apply
   - No data written or modified

4. **Apply operation:**
   - Verifies staging DB is clean (integrity_check, foreign_keys)
   - Reads staging contents (read-only)
   - Snapshots production state (before)
   - Creates timestamped backup via SQLite online-backup API
   - Opens production with FK=ON
   - Begins single transaction
   - Creates/reuses source (idempotent)
   - Inserts all new nodes
   - Inserts all new relationships
   - Commits atomically (or rolls back on ANY failure)
   - Post-apply verification: integrity_check, foreign_key_check, counts
   - Returns deterministic JSON report

5. **Conflict policy:**
   | Item | Policy |
   |------|--------|
   | New node | Inserted with source provenance |
   | Existing identical node | Skipped (no mutation) |
   | Existing node, different content | Conflict — excluded from staging |
   | Existing relationship | Skipped (no mutation) |
   | Duplicate relationship in file | Error (validation rejects) |
   | New source | Created with metadata |
   | Existing source (same name) | Reused (no metadata overwrite) |

6. **Provenance:**
   - Every imported node gets correct `source_id`
   - Source metadata not overwritten on re-import
   - Provenance survives DB reopen (verified by test)

**What does NOT change:**
- SQLite schema
- Contract v1 (`api/contract.py`)
- Importing/verification layer (acceptance pipeline stays separate)
- Production database until explicit `apply`
- Web App (read-only, no import UI yet)

**Tests (50 in `test_phase3.py`):**
- `StagingCreationTests` (7 tests): file creation, new nodes, skip existing, content conflict, determinism, source creation, missing prod DB
- `StagingIsolationTests` (2 tests): production DB unchanged, separate file
- `PreviewTests` (6 tests): reads staging, deterministic, projected counts, missing staging, missing prod, human summary
- `ApplyBasicTests` (5 tests): stages+applies, backup created, post-verify, integrity, human summary
- `ProvenanceTests` (3 tests): source_id on nodes, survives reopen, source metadata not overwritten
- `RollbackTests` (3 tests): unchanged during staging, duplicate ID rollback, staging/dry-run agree
- `IdempotencyTests` (2 tests): same data twice = zero duplicates, same relationships twice
- `MultiDomainApplyTests` (2 tests): all 7 domain types, queryable after apply
- `SecurityTests` (4 tests): invalid data rejected, missing staging, SQL injection safe, no arbitrary path write
- `DBIntegrityTests` (2 tests): integrity_check after apply, FK enforced
- `ContractV1CompatibilityTests` (4 tests): search, get, follow, provenance on imported nodes
- `CLIStageTests` (4 tests): stage valid/invalid, preview valid, apply valid
- `ProductionDBConstantsTests` (6 tests): node/rel/source counts, integrity, FK, hash

**Experimental apply results (copy of production DB):**
- 7 domain nodes imported (person, company, product, document, event, research_paper, location)
- 5 cross-domain relationships imported
- Backup created (39 MB)
- Post-apply: integrity_check=ok, FK violations=0, counts_match=True
- Idempotency: second apply = 0 new nodes, 0 new relationships
- Rollback: conflict excluded from staging, hash unchanged
- Production DB hash: unchanged (`000d4fde...`)

**DB safety:**
- Staging: separate file, never production path
- Dry-run/stage: production DB read-only (`file:...?mode=ro`)
- Apply: backup → atomic transaction → post-verify
- Production DB hash verified before/after all operations

**Rollback:** Delete staging DB. Production untouched until explicit `apply`. Apply failure triggers automatic rollback; production DB remains in pre-apply state.

---

#### Phase 4 — Multi-Domain Test Datasets
**Goal:** Create representative datasets and comprehensive UAT tests.

**Files affected:**
- New: `tests/fixtures/domains/` — test dataset directory
- New: `tests/test_universal_domains.py` — domain-specific tests
- New: `tests/test_domain_uat.py` — UAT lifecycle tests

**Test Datasets (each in its own source file):**

**1. Person Dataset:**
```json
{
  "source": { "name": "tech-persons", "version": "1.0" },
  "nodes": [
    { "id": "person-guido-van-rossum", "type": "person", "name": "Guido van Rossum",
      "description": "Dutch programmer, creator of Python.",
      "birth_year": 1956, "nationality": "Dutch",
      "relationships": [
        { "type": "founded", "target": "python", "label": "created in 1991" },
        { "type": "works_at", "target": "company-google" }
      ]},
    { "id": "person-dan-abramov", "type": "person", "name": "Dan Abramov",
      "description": "React core team member, created Redux.",
      "relationships": [
        { "type": "authored", "target": "concept-react" },
        { "type": "works_at", "target": "company-facebook" }
      ]}
  ]
}
```

**2. Company Dataset:**
```json
{
  "source": { "name": "tech-companies", "version": "1.0" },
  "nodes": [
    { "id": "company-google", "type": "company", "name": "Google",
      "description": "Technology company specializing in internet services.",
      "founded": 1998, "industry": "technology",
      "relationships": [
        { "type": "manufactures", "target": "product-chrome" },
        { "type": "related_to", "target": "company-microsoft", "label": "competitor" }
      ]},
    { "id": "company-microsoft", "type": "company", "name": "Microsoft",
      "description": "Multinational technology corporation.",
      "founded": 1975, "industry": "technology",
      "relationships": [
        { "type": "manufactures", "target": "product-vscode" }
      ]}
  ]
}
```

**3. Product Dataset:**
```json
{
  "source": { "name": "tech-products", "version": "1.0" },
  "nodes": [
    { "id": "product-chrome", "type": "product", "name": "Google Chrome",
      "description": "Web browser developed by Google.",
      "version": "128.0", "category": "browser",
      "relationships": [
        { "type": "depends_on", "target": "technology-chromium" },
        { "type": "manufactures", "target": "company-google", "label": "inverse" }
      ]}
  ]
}
```

**4. Document Dataset:**
```json
{
  "source": { "name": "tech-papers", "version": "1.0" },
  "nodes": [
    { "id": "paper-mapreduce", "type": "research_paper",
      "name": "MapReduce: Simplified Data Processing on Large Clusters",
      "description": "Foundational paper on distributed computing by Google.",
      "authors": ["Jeffrey Dean", "Sanjay Ghemawat"],
      "year": 2004, "venue": "OSDI",
      "relationships": [
        { "type": "authored_by", "target": "person-dean" },
        { "type": "cites", "target": "paper-gfs" },
        { "type": "references", "target": "technology-hadoop" }
      ]}
  ]
}
```

**5. Event Dataset:**
```json
{
  "source": { "name": "tech-events", "version": "1.0" },
  "nodes": [
    { "id": "event-react-conf-2024", "type": "event",
      "name": "React Conf 2024",
      "description": "Annual conference for React developers.",
      "date": "2024-05-15", "location": "Las Vegas, NV",
      "relationships": [
        { "type": "related_to", "target": "technology-react" },
        { "type": "located_in", "target": "location-vegas" }
      ]}
  ]
}
```

**6. Concept Dataset:**
```json
{
  "source": { "name": "cs-concepts", "version": "1.0" },
  "nodes": [
    { "id": "concept-big-o", "type": "concept",
      "name": "Big O Notation",
      "description": "Mathematical notation describing limiting behavior of functions.",
      "category": "algorithms",
      "relationships": [
        { "type": "related_to", "target": "concept-time-complexity" },
        { "type": "instance_of", "target": "concept-notation" }
      ]}
  ]
}
```

**UAT Tests per dataset:**
```python
# For each domain dataset, test the full lifecycle:
# 1. validate → 2. dry-run → 3. preview → 4. apply → 5. search → 6. get → 7. related → 8. follow → 9. provenance → 10. inspect

class TestPersonDomain:
    def test_validate(self): ...
    def test_dry_run(self): ...
    def test_import_and_search(self): ...
    def test_get_person_node(self): ...
    def test_related_persons(self): ...
    def test_follow_works_at(self): ...
    def test_provenance(self): ...
    def test_inspect_shows_persons(self): ...

# Same pattern for Company, Product, Document, Event, Concept, ResearchPaper
```

**What does NOT change:**
- SQLite schema
- Contract v1
- Any existing production code

**Tests required:** 7 domains × 10 lifecycle tests = 70+ tests

**Acceptance criteria:**
- All 7 domain datasets validate successfully
- All 7 domain datasets import successfully (to `:memory:`)
- Full lifecycle test passes for each domain
- Existing test suite still passes

**DB safety:** All tests on `:memory:`.

**Rollback:** Remove test fixtures and test files.

---

#### Phase 5 — CLI Integration
**Goal:** Unified CLI for the complete import workflow.

**Files affected:**
- `ai_engine/__main__.py` — add `import` subcommand
- `ingestion/__main__.py` — extend with `preview`, `apply`, `discard` commands
- `docs/cli.md` — update documentation

**What changes:**
1. Unified CLI workflow:
   ```
   python -m ingestion validate <file>                    # validate source
   python -m ingestion dry-run <file>                     # simulation (no DB)
   python -m ingestion preview <file> --db <staging.db>   # write to staging
   python -m ingestion import <file> --db <prod.db>       # direct import (for trusted sources)
   python -m ingestion apply <file> --db <prod.db>        # staged import with backup
   python -m ingestion inspect <file>                     # source summary
   ```
2. Machine-readable JSON output for all commands
3. Human-readable summary mode (`--human`)
4. Exit codes: 0 = success, 1 = validation error, 2 = import error

**What does NOT change:**
- Contract v1 read operations
- HTTP server
- SDK
- Production database until explicit `import`/`apply`

**Tests required:**
- `test_cli_validate_json_output`
- `test_cli_dry_run_json_output`
- `test_cli_preview_creates_staging`
- `test_cli_apply_produces_backup`
- `test_cli_human_output`
- `test_cli_exit_codes`

**Acceptance criteria:**
- Complete workflow works end-to-end via CLI
- All commands produce valid JSON output
- `--human` mode produces readable output
- Existing CLI commands unchanged

**DB safety:** Same as Phase 3.

**Rollback:** Revert CLI changes.

---

#### Phase 6 — Web App Import UI
**Goal:** Browser-based import interface.

**Files affected:**
- `webapp/index.html` — add import tab
- `http_server/server.py` — add import endpoints
- New: `http_server/import_handler.py` — import request handling

**What changes:**
1. **HTTP endpoints:**
   - `POST /v1/import/validate` — validate uploaded source JSON
   - `POST /v1/import/dry-run` — simulate import, return report
   - `POST /v1/import/preview` — write to staging, return staging ID
   - `POST /v1/import/apply` — promote staging to production
   - `DELETE /v1/import/staging/{id}` — discard staging
   - `GET /v1/import/history` — recent import history
2. **Web app UI (new tab):**
   - File upload area (drag & drop)
   - JSON editor (textarea)
   - Validate button → show errors/warnings
   - Dry-run button → show projected counts
   - Preview button → show staging preview
   - Apply button → confirm → import
   - Import history table
3. **Security:**
   - API key required for all import endpoints
   - Body size limit (same 1 MiB as existing)
   - No filesystem access beyond database writes
   - Source validation prevents code injection

**What does NOT change:**
- Contract v1 read operations (`/v1/execute`)
- Existing web app read-only functionality
- SDK
- Production database (until explicit apply)

**Tests required:**
- `test_import_endpoint_validate`
- `test_import_endpoint_dry_run`
- `test_import_endpoint_preview`
- `test_import_endpoint_apply`
- `test_import_endpoint_rejects_without_auth`
- `test_import_endpoint_body_size_limit`
- `test_webapp_import_tab`

**Acceptance criteria:**
- Import works via browser UI
- Validation errors displayed correctly
- Dry-run shows preview without DB changes
- Apply creates backup + imports atomically
- Existing web app functionality unaffected

**DB safety:** Same as Phase 3. Import endpoints require explicit confirmation.

**Rollback:** Remove import endpoints and UI tab.

---

#### Phase 7 — Security Model + Multi-Source
**Goal:** Harden for external users with multiple independent sources.

**Files affected:**
- `ingestion/validator.py` — add security checks
- `ingestion/importer.py` — add source isolation
- `retrieval/repository.py` — add source query methods
- `api/knowledge_api.py` — add source filtering to search

**What changes:**
1. **Security hardening:**
   - Source metadata length limits
   - Node description content sanitization (no script injection for web display)
   - Relationship target validation (prevent self-referential loops for abuse)
   - Rate limiting for import endpoints
   - Source submission audit log
2. **Multi-source support:**
   - Source listing (`GET /v1/sources`)
   - Node filtering by source (`search(query, source="tech-persons")`)
   - Source metadata queries
   - Cross-source relationship integrity
3. **Provenance enrichment:**
   - Import timestamp with timezone
   - Submitter attribution (optional)
   - Import batch ID (for tracking multi-file imports)

**What does NOT change:**
- SQLite schema (already supports all of this)
- Contract v1 (additive: new optional arguments are backward-compatible)
- Existing import pipeline

**Tests required:**
- `test_source_listing`
- `test_search_by_source`
- `test_security_content_sanitization`
- `test_rate_limiting`
- `test_cross_source_integrity`
- `test_provenance_enrichment`

**Acceptance criteria:**
- External users cannot inject malicious content
- Source isolation prevents cross-contamination
- Provenance is complete and auditable
- All existing tests pass

**DB safety:** Same as Phase 3. Multi-source support is read-only for existing data.

**Rollback:** Revert security and multi-source changes.

---

## F. Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| `PRAGMA foreign_keys` currently OFF at runtime | **High** | Phase 0 documents this. Phase 3 `apply` enforces FK=ON before import. Consider enabling by default. |
| `VALID_TYPES` is a set — changing it may affect Contract v1's `node_type` filter | **Medium** | Additive change only. Contract v1 already validates against `VALID_TYPES` dynamically. New types are automatically accepted. |
| Existing knowledge may have `target_node_id` pointing to non-existent nodes (dangling) | **Low** | Already handled: `target_node_id` is intentionally NOT a FK. Dangling targets are warnings, not errors. |
| External user data may be malformed or malicious | **Medium** | Phase 2 validation + Phase 7 security hardening. Input length limits, content sanitization, API key auth. |
| Staging DB disk space | **Low** | Staging DBs are temporary and discarded. Auto-cleanup after apply/discard. |
| Contract v1 compatibility with new types in search/filter | **Low** | `node_type` filter already accepts any string from `VALID_TYPES`. Adding types is backward-compatible. |
| Web app import UI XSS via node content | **Medium** | Phase 7 content sanitization. Existing webapp already uses `esc()` for XSS prevention. |

---

## G. Proposed Acceptance Tests

### G1. Multi-Domain Import Lifecycle (End-to-End)
```python
@pytest.mark.parametrize("dataset", [
    "person", "company", "product", "document",
    "event", "concept", "research_paper"
])
class TestUniversalImportLifecycle:
    def test_validate_source(self, dataset): ...
    def test_dry_run_safe(self, dataset): ...
    def test_import_to_memory(self, dataset): ...
    def test_search_finds_nodes(self, dataset): ...
    def test_get_node_by_id(self, dataset): ...
    def test_related_nodes(self, dataset): ...
    def test_follow_relationships(self, dataset): ...
    def test_provenance_complete(self, dataset): ...
    def test_inspect_shows_types(self, dataset): ...
```

### G2. Production DB Safety
```python
class TestProductionDBSafety:
    def test_dry_run_does_not_modify_production(self): ...
    def test_validate_does_not_modify_production(self): ...
    def test_inspect_does_not_modify_production(self): ...
    def test_import_creates_backup_before_write(self): ...
    def test_import_rolls_back_on_failure(self): ...
    def test_post_import_verification_passes(self): ...
    def test_existing_knowledge_preserved_after_import(self): ...
```

### G3. Contract v1 Compatibility
```python
class TestContractV1UniversalCompatibility:
    def test_search_new_types(self): ...
    def test_get_new_types(self): ...
    def test_related_new_types(self): ...
    def test_follow_new_relationships(self): ...
    def test_provenance_new_types(self): ...
    def test_inspect_includes_new_types(self): ...
    def test_contract_envelope_unchanged(self): ...
```

### G4. Security
```python
class TestImportSecurity:
    def test_rejects_script_in_description(self): ...
    def test_rejects_oversized_source(self): ...
    def test_rejects_without_api_key(self): ...
    def test_rate_limit_enforced(self): ...
    def test_source_isolation(self): ...
```

### G5. CLI Workflow
```python
class TestCLIWorkflow:
    def test_validate_json_output(self): ...
    def test_dry_run_json_output(self): ...
    def test_preview_staging_output(self): ...
    def test_human_output(self): ...
    def test_exit_codes(self): ...
```

---

## Compatibility Summary

| Component | Requires Change | Impact |
|-----------|----------------|--------|
| SQLite schema | **No** | Types stored as free-form TEXT |
| Contract v1 | **No** | Frozen; new types automatically visible |
| HTTP server | **Yes** (Phase 6) | Add import endpoints |
| SDK | **No** | Search/get/related work on any type |
| Web App | **Yes** (Phase 6) | Add import UI tab |
| CLI | **Yes** (Phase 5) | Add import workflow commands |
| Knowledge Repository | **Minimal** | Add source query methods (Phase 7) |
| Knowledge API | **Minimal** | Add source filtering (Phase 7) |
| Acceptance layer | **No** | Bypassed for external imports |
| Importing layer | **No** | Still used for acceptance pipeline |
| Knowledge compiler | **No** | Still used for CPython extraction |
| Database migration | **No** | Schema already supports arbitrary types |

**Total:** Prefer additive/internal changes. No schema migration. No Contract v1 change. No breaking changes to existing functionality.
