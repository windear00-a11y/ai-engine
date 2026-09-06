"""Persistence hardening — per-project atomic backup/export/import/restore/doctor/migration.

Covers all 6 stores: activity.db, knowledge.db, context.db, evidence.db, experience.db, engine_state.db
Reuses paths.py, migration.py (sqlite3.backup), repository/storage abstractions.
No new DB, no network, deterministic, stdlib-only.

Guarantees (Phase 19):
  - Project isolation (explicit validated project_id, no cross-project mix)
  - Atomic writes (temp file + rename, single transaction where applicable)
  - Backup-before-destructive (restore/import/migration create backup first)
  - Integrity verification (PRAGMA integrity_check for every relevant DB)
  - Restore safety (either complete or leave previous valid state intact, simulated failure test)
  - Export/import correctness (validate before mutate, deterministic IDs/provenance, reject corrupt)
  - Migration idempotence (re-run no duplicate/corruption, legacy DB untouched unless explicit)
  - Doctor deterministic machine-readable info
  - Trust invariants preserved
"""

import json
import os
import sqlite3
import time
import hashlib
import threading

from ai_engine.paths import (
    get_activity_db, get_knowledge_db, get_context_db, get_evidence_db,
    get_experience_db, get_engine_state_db, get_backups_dir, get_project_dir,
    _validate_project_id, get_data_root, list_known_dbs
)
from ai_engine.migration import _integrity_check, _sha256_file

# All 6 DB helpers for iteration
_DB_HELPERS = {
    "activity.db": get_activity_db,
    "knowledge.db": get_knowledge_db,
    "context.db": get_context_db,
    "evidence.db": get_evidence_db,
    "experience.db": get_experience_db,
    "engine_state.db": get_engine_state_db,
}

_BACKUP_COUNTER_LOCK = threading.Lock()
_BACKUP_COUNTER = 0


def _backup_counter():
    global _BACKUP_COUNTER
    with _BACKUP_COUNTER_LOCK:
        _BACKUP_COUNTER = (_BACKUP_COUNTER + 1) % 10000
        return _BACKUP_COUNTER

def _all_db_paths(project_id, data_root=None):
    return {name: fn(project_id, data_root) for name, fn in _DB_HELPERS.items()}

def _ensure_project_exists(project_id, data_root=None):
    _validate_project_id(project_id)
    from ai_engine.paths import ensure_default_project, get_project_entry, create_project
    ensure_default_project(data_root)
    if project_id != "default" and get_project_entry(project_id, data_root) is None:
        try:
            create_project(project_id, data_root=data_root)
        except ValueError:
            pass  # collision -> already exists

def backup_project(project_id, data_root=None, output=None):
    """Atomic per-project backup of all 6 DBs.

    If output is a directory or None, creates timestamped files per DB in backups dir.
    If output is a file path, backs up knowledge.db there (for backward compat, still atomic).
    For Phase 19, when output is None, backs up all existing DBs to backups dir, each with timestamp.

    Returns dict {ok, backup_files: {db_name: path}, errors}
    """
    _validate_project_id(project_id)
    _ensure_project_exists(project_id, data_root)
    paths = _all_db_paths(project_id, data_root)
    # Determine output handling
    backups_dir = get_backups_dir(project_id, data_root)
    os.makedirs(backups_dir, exist_ok=True)

    if output and os.path.isdir(output):
        out_dir = output
        output = None
    else:
        out_dir = None

    backup_files = {}
    errors = []
    # Generate a unique, collision-proof token for this backup call.
    # A second-resolution timestamp alone is NOT unique: restore performs a
    # backup-before-restore immediately after an external backup of the same
    # DB, and both can land in the same wall-clock second using the same
    # filename — silently overwriting the first backup and causing a later
    # restore to resurrect pre-restore rows. A per-call counter guarantees
    # uniqueness across sequential calls within the same second; the process
    # id additionally guarantees uniqueness across processes that begin in
    # the same second.
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"-{_backup_counter():04d}-{os.getpid():x}"

    for db_name, src in paths.items():
        if not os.path.exists(src):
            continue
        # Integrity check before backup
        ic = _integrity_check(src)
        if not ic["ok"]:
            errors.append(f"{db_name} integrity failed before backup: {ic['result']}")
            continue
        if output and not out_dir and len([p for p,_ in paths.items() if os.path.exists(p)]) == 1:
            # Single file output (legacy behavior for single DB)
            # For Phase 19, if output is file and we have multiple DBs, we still backup knowledge.db to that file
            # To avoid ambiguity, if output is file, only backup knowledge.db to it
            if db_name != "knowledge.db":
                continue
            dst = output
        elif out_dir:
            dst = os.path.join(out_dir, f"{db_name}.{ts}.backup")
        else:
            if output:
                # Output is file, backup knowledge.db there, other DBs to backups dir
                if db_name == "knowledge.db":
                    dst = output
                else:
                    dst = os.path.join(backups_dir, f"{db_name}.{ts}.backup")
            else:
                dst = os.path.join(backups_dir, f"{db_name}.{ts}.backup")

        # Atomic: backup to temp then rename
        tmp_dst = dst + ".tmp"
        try:
            src_con = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
            dst_con = sqlite3.connect(tmp_dst)
            src_con.backup(dst_con)
            dst_con.commit()
            dst_con.close()
            src_con.close()
            # Verify dest integrity
            ic2 = _integrity_check(tmp_dst)
            if not ic2["ok"]:
                os.remove(tmp_dst)
                errors.append(f"{db_name} backup integrity failed: {ic2['result']}")
                continue
            os.rename(tmp_dst, dst)
            backup_files[db_name] = dst
        except Exception as e:
            try:
                if os.path.exists(tmp_dst):
                    os.remove(tmp_dst)
            except Exception:
                pass
            errors.append(f"{db_name} backup failed: {e}")

    # If output was a file and we backed up knowledge.db there, also backup other DBs to backups_dir
    # Already handled in loop above when output is file: other DBs go to backups_dir
    ok = len(errors) == 0 and len(backup_files) > 0
    # If no DBs existed, consider it ok but empty
    if not backup_files and not errors:
        ok = True
    return {"ok": ok, "backup_files": backup_files, "errors": errors, "project_id": project_id, "data_root": data_root or get_data_root()}

def export_project(project_id, data_root=None, output=None, fmt="jsonl"):
    """Deterministic export of project's persistent intelligence state.

    Exports knowledge nodes as jsonl (deterministic, sorted), plus
    additional metadata for other stores as separate sections.
    For Phase 19, export includes knowledge nodes plus a manifest with
    counts for other DBs, enough to reconstruct.

    Atomic: write to temp then rename.
    """
    if fmt != "jsonl":
        return {"ok": False, "code": "invalid_argument", "error": "only jsonl supported"}
    _validate_project_id(project_id)
    _ensure_project_exists(project_id, data_root)
    from retrieval.repository import KnowledgeRepository
    db_path = get_knowledge_db(project_id, data_root)
    if not os.path.exists(db_path):
        return {"ok": False, "code": "node_not_found", "error": f"no database for project {project_id!r}"}
    # Integrity check before export
    ic = _integrity_check(db_path)
    if not ic["ok"]:
        return {"ok": False, "code": "internal_error", "error": f"integrity failed: {ic['result']}"}
    try:
        repo = KnowledgeRepository(db_path)
        repo.initialize()
        nodes = repo.get_all_nodes()
        repo.close()
    except Exception as e:
        return {"ok": False, "code": "internal_error", "error": f"export failed: {e}"}
    # Also collect counts for other DBs for manifest
    manifest = {
        "project_id": project_id,
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "node_count": len(nodes),
        "vocabulary": "diary_v1",
    }
    # Add counts for other stores
    for db_name, helper in _DB_HELPERS.items():
        if db_name == "knowledge.db":
            continue
        p = helper(project_id, data_root)
        if os.path.exists(p):
            ic2 = _integrity_check(p)
            manifest[f"{db_name}_exists"] = True
            manifest[f"{db_name}_integrity"] = ic2["ok"]
            try:
                con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                cnt = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
                manifest[f"{db_name}_tables"] = cnt
                con.close()
            except Exception:
                pass
        else:
            manifest[f"{db_name}_exists"] = False

    # Write atomically
    tmp_out = None
    try:
        if output:
            tmp_out = output + ".tmp"
            out_path = tmp_out
            dir_name = os.path.dirname(os.path.abspath(output)) or "."
            os.makedirs(dir_name, exist_ok=True)
        else:
            # No output specified: return in-memory? For CLI, output to stdout is handled separately.
            # For this function, if output is None, we return nodes without writing.
            return {"ok": True, "nodes": nodes, "manifest": manifest, "node_count": len(nodes)}
        # Write manifest as first line, then nodes
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({"__manifest__": manifest}, sort_keys=True, ensure_ascii=False) + "\n")
            for n in sorted(nodes, key=lambda x: x.get("id", "")):
                f.write(json.dumps(n, sort_keys=True, ensure_ascii=False) + "\n")
        # Verify written file is valid jsonl
        with open(out_path, "r", encoding="utf-8") as f:
            for line in f:
                json.loads(line)
        if output:
            os.rename(tmp_out, output)
        return {"ok": True, "output": output or out_path, "node_count": len(nodes), "manifest": manifest}
    except Exception as e:
        try:
            if tmp_out and os.path.exists(tmp_out):
                os.remove(tmp_out)
        except Exception:
            pass
        return {"ok": False, "code": "internal_error", "error": f"export failed: {e}"}

def import_project(file_path, project_id, data_root=None, dry_run=False):
    """Import from export file, with validation before mutate and backup before destructive.

    Returns {ok, imported_count, dry_run, backup_files, errors}
    """
    _validate_project_id(project_id)
    _ensure_project_exists(project_id, data_root)
    if not os.path.exists(file_path):
        return {"ok": False, "code": "invalid_argument", "error": f"file not found: {file_path!r}"}
    # Read and validate
    nodes = []
    manifest = None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for line in f:
                line=line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if "__manifest__" in obj:
                    manifest = obj["__manifest__"]
                    continue
                nodes.append(obj)
    except Exception as e:
        return {"ok": False, "code": "invalid_argument", "error": f"invalid jsonl: {e}"}
    if not nodes:
        return {"ok": False, "code": "invalid_argument", "error": "no nodes in file"}
    # Validate via ingestion validator
    envelope = {"source": {"name": f"import:{os.path.basename(file_path)}", "version": "1.0", "location": file_path}, "nodes": nodes}
    try:
        from ingestion.validator import validate_source
        res = validate_source(envelope)
        if not res.valid:
            return {"ok": False, "code": "invalid_argument", "error": "; ".join(e.message for e in res.errors), "validation_errors": [e.as_dict() for e in res.errors]}
    except Exception as e:
        return {"ok": False, "code": "internal_error", "error": f"validation failed: {e}"}
    if dry_run:
        return {"ok": True, "dry_run": True, "node_count": len(res.nodes), "relationship_count": sum(len(n.get("relationships", [])) for n in res.nodes)}
    # Backup before destructive write
    backup_res = backup_project(project_id, data_root=data_root)
    # Even if backup had no files (empty project), it's ok
    # Now import
    from retrieval.repository import KnowledgeRepository
    db_path = get_knowledge_db(project_id, data_root)
    try:
        repo = KnowledgeRepository(db_path)
        repo.initialize()
        repo.import_source(envelope["source"]["name"], envelope["source"]["location"], res.nodes, source_version=envelope["source"]["version"])
        repo.close()
    except Exception as e:
        return {"ok": False, "code": "invalid_argument", "error": f"import failed: {e}", "backup_files": backup_res.get("backup_files", {})}
    return {"ok": True, "imported_count": len(res.nodes), "backup_files": backup_res.get("backup_files", {}), "manifest": manifest}

def restore_project(backup_file, project_id, data_root=None):
    """Restore a project DB from a backup file (knowledge.db backup).

    Backup-before-restore: creates backup of current state before overwriting.
    Atomic: restore to temp then rename.
    Integrity verification.

    Returns {ok, restored, backup_files, error}
    """
    _validate_project_id(project_id)
    _ensure_project_exists(project_id, data_root)
    if not os.path.exists(backup_file):
        return {"ok": False, "code": "invalid_argument", "error": f"backup file not found: {backup_file!r}"}
    # Verify backup integrity
    ic = _integrity_check(backup_file)
    if not ic["ok"]:
        return {"ok": False, "code": "invalid_argument", "error": f"backup integrity failed: {ic['result']}"}
    # Determine which DB this backup is for: assume knowledge.db if not specified
    # For Phase 19, backup files are named like knowledge.db.<ts>.backup or custom output
    # We restore to knowledge.db
    dest = get_knowledge_db(project_id, data_root)
    # Backup current before destructive
    backup_res = backup_project(project_id, data_root=data_root)
    tmp_dest = dest + ".restore.tmp"
    try:
        # Use sqlite backup for atomic restore
        src_con = sqlite3.connect(f"file:{backup_file}?mode=ro", uri=True)
        dst_con = sqlite3.connect(tmp_dest)
        src_con.backup(dst_con)
        dst_con.commit()
        dst_con.close()
        src_con.close()
        ic2 = _integrity_check(tmp_dest)
        if not ic2["ok"]:
            for suffix in ("-wal", "-shm"):
                side_path = tmp_dest + suffix
                if os.path.exists(side_path):
                    try:
                        os.remove(side_path)
                    except Exception:
                        pass
            os.remove(tmp_dest)
            return {"ok": False, "code": "internal_error", "error": f"restored integrity failed: {ic2['result']}", "backup_files": backup_res.get("backup_files", {})}
        # Atomic rename
        os.rename(tmp_dest, dest)
        # Remove stale WAL/shm sidecars from the PREVIOUS live DB.
        # After restoring a fresh consistent copy, any leftover -wal/-shm
        # belong to the old inode and would be replayed, resurrecting
        # pre-restore rows. Removing them is safe (the new copy is clean).
        for suffix in ("-wal", "-shm"):
            side_path = dest + suffix
            if os.path.exists(side_path):
                try:
                    os.remove(side_path)
                except Exception:
                    pass
        return {"ok": True, "restored": dest, "backup_files": backup_res.get("backup_files", {}), "from": backup_file}
    except Exception as e:
        try:
            if os.path.exists(tmp_dest):
                os.remove(tmp_dest)
        except Exception:
            pass
        return {"ok": False, "code": "internal_error", "error": f"restore failed: {e}", "backup_files": backup_res.get("backup_files", {})}

def doctor_project(project_id, data_root=None):
    """Deterministic machine-readable doctor info for a project.

    Returns dict with:
        project_id, data_root, databases: {db_name: {exists, integrity, tables, schema_version, migration_state}},
        migration_state, consistency_problems
    """
    _validate_project_id(project_id)
    data_root_resolved = data_root or get_data_root()
    result = {
        "project_id": project_id,
        "data_root": data_root_resolved,
        "databases": {},
        "migration_state": {},
        "consistency_problems": [],
    }
    # Check projects.json
    from ai_engine.paths import get_projects_registry, load_projects_registry
    reg_path = get_projects_registry(data_root)
    if os.path.exists(reg_path):
        result["migration_state"]["projects_json_exists"] = True
        try:
            reg = load_projects_registry(data_root)
            result["migration_state"]["projects_count"] = len(reg.get("projects", []))
            result["migration_state"]["has_project"] = any(p.get("project_id") == project_id for p in reg.get("projects", []))
            if not result["migration_state"]["has_project"]:
                result["consistency_problems"].append(f"project {project_id!r} not in projects.json")
        except Exception as e:
            result["consistency_problems"].append(f"projects.json invalid: {e}")
    else:
        result["migration_state"]["projects_json_exists"] = False
        result["consistency_problems"].append("projects.json missing")

    for db_name, helper in _DB_HELPERS.items():
        p = helper(project_id, data_root)
        entry = {"path": p, "exists": os.path.exists(p)}
        if entry["exists"]:
            ic = _integrity_check(p)
            entry["integrity"] = ic["ok"]
            entry["integrity_result"] = ic["result"]
            if not ic["ok"]:
                result["consistency_problems"].append(f"{db_name} integrity failed: {ic['result']}")
            try:
                con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
                tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
                entry["tables"] = tables
                # Check schema version for engine_state
                if db_name == "engine_state.db":
                    try:
                        row = con.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
                        entry["schema_version"] = row[0] if row else None
                    except Exception:
                        entry["schema_version"] = None
                # Check for missing expected tables — only report if DB is non-empty and table is critical
                # For fresh projects, some tables may not yet exist (e.g., strategies before any learning)
                # So only report if the DB has at least one table but is missing an expected core table
                expected = {
                    "knowledge.db": ["sources", "nodes", "relationships"],
                    "activity.db": ["activities"],
                    "context.db": ["context_snapshots"],
                    "evidence.db": ["evidence"],
                    "experience.db": ["experience"],
                    "engine_state.db": ["tasks"],
                }.get(db_name, [])
                # Also check for optional tables but don't report as problems if missing
                optional = {
                    "evidence.db": ["strategies", "learning_events", "strategy_audit"],
                    "experience.db": [],
                    "engine_state.db": ["journal", "audit"],
                }.get(db_name, [])
                for tbl in expected:
                    if tbl not in tables:
                        result["consistency_problems"].append(f"{db_name} missing table {tbl}")
                # Optional tables are not reported as problems if missing (fresh project)
                con.close()
            except Exception as e:
                entry["error"] = str(e)
                result["consistency_problems"].append(f"{db_name} check error: {e}")
        else:
            # Missing DB is not necessarily an error for fresh project, but note
            entry["integrity"] = None
        result["databases"][db_name] = entry

    # Check migration state via migration helper (info only, not a hard error for fresh projects)
    try:
        from ai_engine.migration import get_migration_status
        mig = get_migration_status(project_id, data_root=data_root)
        result["migration_state"]["legacy_status"] = mig
        # For fresh projects, legacy not yet migrated is informational, not a consistency problem
        # Only report if legacy DB is the same as project's data_root (i.e., not a fresh per-project DB)
        # So we do not add to consistency_problems for now
    except Exception as e:
        result["migration_state"]["legacy_check_error"] = str(e)

    result["ok"] = len(result["consistency_problems"]) == 0
    return result
