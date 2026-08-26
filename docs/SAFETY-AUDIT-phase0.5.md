# Phase 0.5 Safety Audit — Universal Data Ingestion v1

> **Date:** 2026-08-25  
> **Type:** READ-ONLY audit. No source files, database, or backups modified.  
> **Scope:** Complete database write/import path, FK enforcement, failure modes

---

## Critical Finding: Corrected

**Previous claim:** "Production SQLite connections currently use PRAGMA foreign_keys = OFF"  
**Actual finding:** This is **incorrect**. `PRAGMA foreign_keys=ON` is already set on every `KnowledgeRepository` connection at `retrieval/repository.py:80`. All production code paths that touch the database go through `KnowledgeRepository`, which always enables FK enforcement. The production import path (`importing/importer.py:330`) explicitly verifies FK=ON and refuses to proceed if it is not.

The only raw `sqlite3.connect()` calls (which default to FK=OFF) are:
1. `backup_database()` (`importing/importer.py:69-70`) — short-lived backup connections that never write to knowledge tables
2. `graph_report.py:103` and `backfill.py:301` — read-only connections using `mode=ro` URI

---

## A. Findings

### A1. SQLite Connection Architecture

| Component | Connection Method | FK Status | Writes? |
|-----------|------------------|-----------|---------|
| `KnowledgeRepository.__init__()` | `sqlite3.connect(db_path)` + `PRAGMA foreign_keys=ON` | **ON** | Yes |
| HTTP server (`server.py:140`) | `KnowledgeRepository(db, check_same_thread=False)` | **ON** | No (read-only API) |
| CLI ingestion (`ingestion/__main__.py:92`) | `KnowledgeRepository(db)` | **ON** | Yes |
| Production import (`importing/importer.py:327`) | `KnowledgeRepository(db_path)` | **ON** | Yes |
| Post-import verify (`importing/importer.py:168`) | `KnowledgeRepository(db_path)` | **ON** | No (read-only) |
| Dry-run (`importing/dry_run.py:248`) | `KnowledgeRepository(":memory:")` | **ON** | In-memory only |
| Backup source (`importing/importer.py:69`) | Raw `sqlite3.connect(db_path)` | **OFF** | No (backup API only) |
| Backup dest (`importing/importer.py:70`) | Raw `sqlite3.connect(path)` | **OFF** | Backup target only |
| Graph report (`graph_report.py:103`) | Raw `sqlite3.connect(mode=ro)` | **OFF** | No (read-only URI) |
| Backfill (`backfill.py:301`) | Raw `sqlite3.connect(mode=ro)` | **OFF** | No (read-only URI) |
| `_snapshot()` (`importing/importer.py:87`) | Raw `sqlite3.connect(mode=ro)` | **OFF** | No (read-only URI) |

**Conclusion:** FK enforcement is already active on every write path. The raw connections with FK=OFF are either read-only or backup-only.

### A2. Foreign Key Constraints in Schema

```sql
-- Source → Node FK (ENFORCED)
FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE

-- Node → Relationship FK (ENFORCED)
FOREIGN KEY (source_node_id) REFERENCES nodes(id) ON DELETE CASCADE

-- Target → Node (INTENTIONALLY NOT AN FK)
-- target_node_id has NO foreign key constraint
-- This allows forward/dangling references (design decision)
```

**Current production DB violations:**
| Check | Count | Status |
|-------|-------|--------|
| Orphan nodes (source_id → nonexistent source) | 0 | CLEAN |
| Null source_id nodes | 0 | CLEAN |
| Orphan rel sources (source_node_id → nonexistent node) | 0 | CLEAN |
| Dangling targets (target_node_id → nonexistent node) | 8 | EXPECTED (not an FK) |
| Duplicate node IDs | 0 | CLEAN |
| Duplicate relationships | 0 | CLEAN |

**FK enforcement verified working:** Enabling FK=ON on the production DB copy correctly rejects inserts with invalid `source_id` or `source_node_id`.

### A3. Production DB Baseline (Immutable)

```
SHA-256:        000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91
File size:      39,256,064 bytes
integrity_check: ok
Sources:        6
Nodes:          4,846 (concept: 185, dependency: 136, entity: 1,092, example: 3,025, procedure: 81, rule: 2, technology: 325)
Relationships:  1,338 (depends_on: 150, example_of: 1,143, extends: 17, implements: 1, instance_of: 1, part_of: 11, references: 5, related_to: 9, uses: 1)
FK violations:  0
```

**Post-audit verification:** SHA-256 matches — production DB is byte-identical after all audit operations.

### A4. Complete Write Path Trace

Every write to the knowledge database follows this architecture:

```
External input
    ↓
Validation layer (ingestion/validator.py or importing/verifier.py)
    ↓
KnowledgeRepository.__init__() → PRAGMA foreign_keys=ON  ←── FK enforcement activated here
    ↓
Atomic transaction (with self.conn: or repo.transaction())
    ↓
INSERT INTO sources / nodes / relationships (parameterized SQL)
    ↓
Commit or rollback
    ↓
Post-import verification (importing/importer.py)
```

**No write path bypasses FK enforcement.** The only exception is `backup_database()` which uses raw connections but only for the SQLite online-backup API (no knowledge table writes).

### A5. Transaction Boundaries

| Write Path | Transaction Type | Rollback on Failure | Atomic? |
|------------|-----------------|---------------------|---------|
| `add_source()` | Implicit (`with self.conn:`) | Yes | Per-call |
| `add_node()` | Implicit (`with self.conn:`) | Yes | Per-call |
| `add_relationship()` | Implicit (`with self.conn:`) | Yes | Per-call |
| `import_node()` | Implicit (`with self.conn:`) | Yes | Per source |
| `import_source()` | Implicit (`with self.conn:`) | Yes | Per source |
| `clear()` | Implicit (`with self.conn:`) | Yes | Full wipe |
| `apply_plan()` | Explicit (`repo.transaction()`) | Yes | Per plan |
| `import_plan()` (orchestrator) | Delegates to `apply_plan()` | Yes | Per plan |
| `KnowledgeStore._load_file()` | Via `import_node()` | Yes | Per file |

**No partial writes possible.** Every write path uses transactions with automatic rollback.

### A6. Failure Simulation Results (Isolated Copy)

| Failure Mode | FK=ON Behavior | DB State After | Correct? |
|-------------|---------------|----------------|----------|
| Invalid `source_id` (FK violation) | REJECTED | Unchanged | Yes |
| Invalid `source_node_id` (FK violation) | REJECTED | Unchanged | Yes |
| Dangling `target_node_id` (not an FK) | ALLOWED | Modified (new row) | Yes (design) |
| Duplicate relationship (UNIQUE) | REJECTED | Unchanged | Yes |
| Duplicate node ID (PK) | REJECTED | Unchanged | Yes |
| NULL `source_id` (FK allows NULL) | ALLOWED | Modified (new row) | Yes (correct) |
| Interrupted transaction (no commit) | No change | Unchanged | Yes |
| Invalid JSON in metadata column | ALLOWED (TEXT column) | Modified (new row) | App-layer needed |

**Key insight:** SQLite schema does NOT enforce NOT NULL on `name`/`description` columns. App-layer validation (`ingestion/validator.py`) handles this. The schema only enforces PKs, FKs, and UNIQUE constraints.

---

## B. Current Risks

| Risk | Severity | Status | Notes |
|------|----------|--------|-------|
| FK=OFF at connection time | **RESOLVED** | FK=ON is already the default in `KnowledgeRepository` | Documentation was inaccurate |
| `target_node_id` not an FK | **Low (by design)** | Allows dangling references | Production import refuses them; dry-run allows them |
| `name`/`description` NULL allowed at DB level | **Low** | App-layer validation catches this | `ingestion/validator.py` enforces non-empty |
| Metadata JSON not validated at DB level | **Low** | App-layer validation catches this | `ingestion/validator.py` validates structure |
| Raw `sqlite3.connect()` in backup code | **Low** | FK=OFF on backup connections | Short-lived, read/backup-only, no knowledge table writes |
| `check_same_thread=False` in HTTP server | **Low** | Mitigated by `_execute_lock` | Required for ThreadingHTTPServer |
| `clear()` is destructive | **Low** | Gated by explicit `clear=True` parameter | Never exposed via API/HTTP |

---

## C. Experimental Results

### C1. FK Enforcement Test (in-memory)
```
FK=ON:  Invalid source_id INSERT → REJECTED (FOREIGN KEY constraint failed) ✓
FK=OFF: Invalid source_id INSERT → ALLOWED (violation permitted) 
```
**Conclusion:** FK enforcement works correctly when enabled.

### C2. FK=ON on Production DB Copy
```
All read operations (search, get, follow, provenance, inspect): PASS ✓
Valid insert with FK=ON: SUCCESS ✓
Invalid source_id with FK=ON: REJECTED ✓
Invalid source_node_id with FK=ON: REJECTED ✓
Dangling target_node_id with FK=ON: ALLOWED (correct - not an FK) ✓
```
**Conclusion:** Enabling FK=ON does not break any read operations. All existing data is valid.

### C3. Test Suite with FK=ON
```
tests.test_sqlite_repository:    PASS ✓
tests.test_ingestion:            PASS ✓
tests.test_importing:            PASS ✓
tests.test_importer:             PASS ✓
tests.test_contract_v1:          PASS ✓
tests.test_api:                  PASS ✓
tests.test_knowledge:            PASS ✓
tests.test_acceptance:           PASS ✓
```
**Conclusion:** All critical test modules pass with FK=ON.

### C4. Production DB Immutability
```
Pre-audit SHA-256:  000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91
Post-audit SHA-256: 000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91
Match: TRUE ✓
```
**Conclusion:** Production DB was not modified during the audit.

---

## D. Recommended Fix

### D1. Documentation Correction

The MILESTONE document (`MILESTONE-universal-data-ingestion-v1.md`) contains an incorrect claim:
- Line 280: `"PRAGMA foreign_keys documented (currently 0 at connection time -- known issue)"`
- Line 773: Risk table marks `PRAGMA foreign_keys currently OFF at runtime` as **High**

**This should be corrected.** FK=ON is already the default. The "known issue" does not exist.

### D2. No Code Changes Needed for FK Enforcement

FK enforcement is already correct and complete. No changes to `retrieval/repository.py` are needed.

### D3. Optional Hardening (Not Required for v1)

| Enhancement | Priority | Effort | Risk |
|-------------|----------|--------|------|
| Add `NOT NULL` to `nodes.name` and `nodes.description` in schema | Low | Low (migration needed) | Could break existing reads |
| Add JSON validation for `nodes.metadata` column | Low | Low | App-layer already handles |
| Use `KnowledgeRepository` instead of raw `sqlite3.connect()` in backup code | Low | Low | Backup code is isolated |
| Add `PRAGMA foreign_keys=ON` to raw connections for defense-in-depth | Low | Trivial | No impact on read-only connections |

---

## E. Required Tests

### E1. FK Enforcement Tests (exist already)
- `test_foreign_keys_enabled` in `test_sqlite_repository.py:42` — verifies FK=ON
- `test_primary_keys_enforced` in `test_sqlite_repository.py:47` — verifies PK constraint

### E2. Recommended Additional Tests
```python
# New tests to add to test_sqlite_repository.py
class FKEnforcementTests(unittest.TestCase):
    def test_invalid_source_id_rejected(self):
        """INSERT with non-existent source_id fails with FK=ON"""
        
    def test_invalid_source_node_id_rejected(self):
        """INSERT relationship with non-existent source_node_id fails"""
        
    def test_dangling_target_allowed(self):
        """INSERT relationship with non-existent target succeeds (target is not FK)"""
        
    def test_null_source_id_allowed(self):
        """INSERT with NULL source_id succeeds (NULL is allowed in FK)"""

# New tests for import safety
class ImportSafetyTests(unittest.TestCase):
    def test_import_refuses_without_fk(self):
        """Production import refuses when FK is disabled"""
        
    def test_import_creates_backup_before_write(self):
        """Backup exists before any import writes"""
        
    def test_import_rolls_back_on_failure(self):
        """Failed import leaves DB unchanged"""
        
    def test_post_import_verification_passes(self):
        """Post-import check verifies FK, provenance, traversal"""
```

### E3. Production DB Immutability Tests (exist already)
- `test_dry_run_never_touches_production_db` in `test_importing.py:423`
- Multiple hash-check tests in `test_knowledge_client.py`, `test_session.py`, `test_webapp.py`, `test_external_http_client.py`

---

## F. Go/No-Go Decision

### Verdict: **GO** — Safe to proceed to Universal Data Ingestion Phase 1

**Rationale:**

1. **FK enforcement is already ON.** The previous concern about FK=OFF was based on documentation that did not match the code. `KnowledgeRepository.__init__()` at `retrieval/repository.py:80` sets `PRAGMA foreign_keys=ON` on every connection.

2. **Production DB has zero FK violations.** All existing data satisfies all FK constraints. Enabling FK=ON does not break any reads or writes.

3. **All write paths are already safe.** Every write path uses transactions with rollback, parameterized SQL, and pre-write validation. The production import path additionally requires backup creation and post-import verification.

4. **No code changes needed.** The current architecture already supports universal data ingestion through the existing ingestion pipeline (`ingestion/`) with its validation and atomic import mechanisms.

5. **Test suite passes with FK=ON.** All critical test modules pass, confirming backward compatibility.

6. **Production DB is immutable.** SHA-256 verified before and after all audit operations.

### What Should Remain Untouched
- `database/knowledge.db` — production data
- `database/knowledge.db.backup` — existing backup
- `database/backups/` — timestamped backups
- `retrieval/repository.py` — FK enforcement already correct
- `api/contract.py` — Contract v1 frozen
- `api/knowledge_api.py` — read-only operations unchanged
- `http_server/server.py` — no changes needed
- `knowledge_client/` — SDK unchanged

### What Would Need Modification for Phase 1
- `retrieval/knowledge.py` — extend `VALID_TYPES` and `RELATIONSHIP_KINDS` (additive)
- `ingestion/source_format.py` — document universal format (documentation only)
- `knowledge/SCHEMA.md` — update schema documentation
- New test fixtures in `tests/fixtures/domains/` — multi-domain test data

### Files That Would Need Modification for Later Phases
- `ingestion/__main__.py` — Phase 5 CLI integration
- `http_server/server.py` — Phase 6 import endpoints
- `webapp/index.html` — Phase 6 import UI
- `ingestion/validator.py` — Phase 7 security hardening

---

## G. Production DB Safety Summary

```
Pre-audit state:
  SHA-256:        000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91
  File size:      39,256,064 bytes
  integrity:      ok
  FK violations:  0
  Node count:     4,846
  Rel count:      1,338
  Source count:   6

Post-audit state:
  SHA-256:        000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91
  File size:      39,256,064 bytes
  integrity:      ok
  FK violations:  0
  Node count:     4,846
  Rel count:      1,338
  Source count:   6

Status: BYTE-IDENTICAL ✓
```
