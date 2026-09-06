"""Phase 0 migration: legacy database/*.db -> per-project data dir.

Uses sqlite3.backup (same pattern as importing/importer.py:49).
Source databases are never modified, never deleted, never moved.
Migration is safe to repeat (idempotent).

This module is read-only with respect to intelligence/* and
tools/permissions — it only copies SQLite files.
"""

import hashlib
import os
import sqlite3
import time

from ai_engine.paths import (
    DEFAULT_PROJECT_ID,
    get_backups_dir,
    get_context_db,
    get_engine_state_db,
    get_evidence_db,
    get_experience_db,
    get_knowledge_db,
    get_project_dir,
    get_snapshots_dir,
)

_LEGACY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "database")

# Databases that may live in legacy database/ directory.
_LEGACY_DBS = ("knowledge.db", "context.db", "evidence.db", "experience.db", "engine_state.db")

# Map legacy file name -> helper that returns dest path for a project.
_DB_HELPERS = {
    "knowledge.db": get_knowledge_db,
    "context.db": get_context_db,
    "evidence.db": get_evidence_db,
    "experience.db": get_experience_db,
    "engine_state.db": get_engine_state_db,
}


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _integrity_check(path):
    """Return {ok: bool, result: str} for PRAGMA integrity_check on `path`."""
    # Use file: URI with mode=ro to ensure we do not create a DB.
    uri = f"file:{path}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
    except Exception as e:
        return {"ok": False, "result": str(e)}
    try:
        row = con.execute("PRAGMA integrity_check").fetchone()
        ok = bool(row and row[0] == "ok")
        return {"ok": ok, "result": row[0] if row else "no result"}
    except Exception as e:
        return {"ok": False, "result": str(e)}
    finally:
        try:
            con.close()
        except Exception:
            pass


def _count_tables(path, table):
    uri = f"file:{path}?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True)
        cnt = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        con.close()
        return cnt
    except Exception:
        return None


def _same_logical_content(src, dst):
    """Check whether two SQLite files contain the same logical content.

    For knowledge.db we compare counts of key tables and hash of sorted node ids.
    For generic DBs we compare counts of all user tables.

    Returns True if logically identical, False otherwise.
    """
    try:
        # Quick: both must pass integrity
        if not _integrity_check(src)["ok"] or not _integrity_check(dst)["ok"]:
            return False
        src_uri = f"file:{src}?mode=ro"
        dst_uri = f"file:{dst}?mode=ro"
        s_con = sqlite3.connect(src_uri, uri=True)
        d_con = sqlite3.connect(dst_uri, uri=True)
        try:
            s_tables = [r[0] for r in s_con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
            d_tables = [r[0] for r in d_con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()]
            if set(s_tables) != set(d_tables):
                return False
            for tbl in s_tables:
                s_cnt = s_con.execute(f"SELECT COUNT(*) FROM \"{tbl}\"").fetchone()[0]
                d_cnt = d_con.execute(f"SELECT COUNT(*) FROM \"{tbl}\"").fetchone()[0]
                if s_cnt != d_cnt:
                    return False
            # For knowledge.db additionally compare set of node ids (logical content vs same-count different rows)
            if "nodes" in s_tables:
                s_ids = sorted([r[0] for r in s_con.execute("SELECT id FROM nodes").fetchall()])
                d_ids = sorted([r[0] for r in d_con.execute("SELECT id FROM nodes").fetchall()])
                if s_ids != d_ids:
                    return False
            return True
        finally:
            s_con.close()
            d_con.close()
    except Exception:
        return False


def migrate_database(source_path, dest_path):
    """Copy a single SQLite file via sqlite3.backup.

    Source must exist and be a file (or symlink to file). Dest parent is created.
    If dest already exists and passes integrity_check and has same SHA as source,
    the copy is skipped (idempotent). If dest exists with different content,
    the function FAILS CLOSED (does not overwrite) and returns error.

    Returns dict with keys: ok, skipped (bool), source, dest, error (if not ok),
    source_sha, dest_sha (when applicable), integrity (when checked).
    """
    if not isinstance(source_path, str) or not source_path:
        return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": "source_path must be non-empty string"}
    if not isinstance(dest_path, str) or not dest_path:
        return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": "dest_path must be non-empty string"}

    # Source must exist
    if not os.path.exists(source_path):
        return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": f"source not found: {source_path!r}"}
    if not os.path.isfile(source_path) and not os.path.islink(source_path):
        return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": f"source is not a file: {source_path!r}"}

    # If dest is a symlink, fail closed unless it already points inside data dir?
    # For Phase 0 we treat any existing dest symlink as error to avoid confusion.
    if os.path.islink(dest_path):
        return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": f"dest is a symlink, refusing to overwrite: {dest_path!r}"}

    # Ensure dest parent exists
    dest_dir = os.path.dirname(os.path.abspath(dest_path))
    if dest_dir and not os.path.isdir(dest_dir):
        os.makedirs(dest_dir, exist_ok=True)

    # If dest exists: check idempotence
    if os.path.exists(dest_path):
        if not os.path.isfile(dest_path):
            return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": f"dest exists and is not a file: {dest_path!r}"}
        try:
            source_sha = _sha256_file(source_path)
            dest_sha = _sha256_file(dest_path)
        except Exception as e:
            return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": f"hash failed: {e}"}
        if source_sha == dest_sha:
            integ = _integrity_check(dest_path)
            if integ["ok"]:
                return {"ok": True, "skipped": True, "source": source_path, "dest": dest_path, "source_sha": source_sha, "dest_sha": dest_sha, "integrity": integ}
            else:
                return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": f"dest integrity failed: {integ['result']}", "source_sha": source_sha, "dest_sha": dest_sha}
        # SHA differs: sqlite3.backup produces different file bytes even for identical
        # logical content (different page layout). So check logical equality (counts)
        # before declaring collision. If logical content matches and dest is healthy,
        # treat as idempotent (backup case).
        if _same_logical_content(source_path, dest_path):
            integ = _integrity_check(dest_path)
            if integ["ok"]:
                return {"ok": True, "skipped": True, "source": source_path, "dest": dest_path, "source_sha": source_sha, "dest_sha": dest_sha, "integrity": integ, "note": "logically identical via backup"}
        # Genuine different content: fail closed
        return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": "dest exists with different content (collision); refusing to overwrite", "source_sha": source_sha, "dest_sha": dest_sha}

    # Dest does not exist: perform sqlite3.backup copy (safe, never modifies source)
    try:
        src_sha_before = _sha256_file(source_path)
    except Exception as e:
        return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": f"source hash failed: {e}"}

    # Fast path: if source file is not a SQLite DB (e.g. empty), copy bytes directly?
    # We try backup; if source is not a SQLite file, backup will create an empty dest;
    # we instead do file copy fallback but prefer backup for DB files.
    # Detect by trying integrity_check on source; if ok, use backup, else byte-copy.
    src_integ = _integrity_check(source_path)
    use_backup = src_integ["ok"]
    try:
        if use_backup:
            # Use sqlite3 backup API
            src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
            dst = sqlite3.connect(dest_path)
            try:
                src.backup(dst)
                dst.commit()
            finally:
                dst.close()
                src.close()
        else:
            # Not a SQLite DB (maybe legacy knowledge.db.backup or non-DB file) -> byte copy
            # For Phase 0 we only migrate .db SQLite files, so this branch is defensive.
            with open(source_path, "rb") as sf, open(dest_path, "wb") as df:
                for chunk in iter(lambda: sf.read(1 << 20), b""):
                    df.write(chunk)
            # No integrity to verify for non-DB
            return {"ok": True, "skipped": False, "source": source_path, "dest": dest_path, "source_sha": src_sha_before, "dest_sha": _sha256_file(dest_path), "integrity": {"ok": True, "result": "byte-copy"}}
    except Exception as e:
        # Clean up partial dest on failure (best effort)
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except Exception:
            pass
        return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": f"backup failed: {e}", "source_sha": src_sha_before}

    # Verify dest integrity
    dest_integ = _integrity_check(dest_path)
    if not dest_integ["ok"]:
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except Exception:
            pass
        return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": f"dest integrity failed after copy: {dest_integ['result']}", "source_sha": src_sha_before}

    # Verify source unchanged (hash same before/after)
    try:
        src_sha_after = _sha256_file(source_path)
        dest_sha = _sha256_file(dest_path)
    except Exception as e:
        return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": f"post-copy hash failed: {e}", "source_sha": src_sha_before}

    if src_sha_before != src_sha_after:
        # Source changed during copy (unlikely for read-only source, but fail closed)
        try:
            if os.path.exists(dest_path):
                os.remove(dest_path)
        except Exception:
            pass
        return {"ok": False, "skipped": False, "source": source_path, "dest": dest_path, "error": "source changed during copy; dest removed"}

    return {"ok": True, "skipped": False, "source": source_path, "dest": dest_path, "source_sha": src_sha_after, "dest_sha": dest_sha, "integrity": dest_integ}


def get_migration_status(project_id, source_root=None, data_root=None):
    """Read-only status for a project's legacy -> per-project migration.

    Never writes. Returns dict with per-db entries:
        {db_name: {"source": path, "source_exists": bool, "dest": path, "dest_exists": bool,
                   "source_sha": ..., "dest_sha": ..., "integrity": ..., "counts": {...}}}
    """
    if source_root is None:
        source_root = _LEGACY_DIR
    helpers = _DB_HELPERS
    out = {"project_id": project_id, "source_root": source_root, "data_root": data_root, "dbs": {}}
    for db_name, helper in helpers.items():
        src = os.path.join(source_root, db_name)
        try:
            dst = helper(project_id, data_root)
        except Exception as e:
            dst = f"<invalid: {e}>"
        entry = {"source": src, "source_exists": os.path.exists(src), "dest": dst, "dest_exists": os.path.exists(dst) if isinstance(dst, str) else False}
        if entry["source_exists"]:
            try:
                entry["source_sha"] = _sha256_file(src)
                entry["source_integrity"] = _integrity_check(src)
                # Counts for known tables (best effort)
                if db_name == "knowledge.db":
                    entry["counts"] = {
                        "nodes": _count_tables(src, "nodes"),
                        "relationships": _count_tables(src, "relationships"),
                        "sources": _count_tables(src, "sources"),
                    }
                elif db_name == "context.db":
                    entry["counts"] = {}
                    try:
                        uri = f"file:{src}?mode=ro"
                        con = sqlite3.connect(uri, uri=True)
                        row = con.execute("SELECT COUNT(*) FROM context_snapshots").fetchone()
                        entry["counts"]["context_snapshots"] = row[0] if row else None
                        con.close()
                    except Exception:
                        pass
            except Exception as e:
                entry["source_error"] = str(e)
        if entry["dest_exists"]:
            try:
                entry["dest_sha"] = _sha256_file(dst)
                entry["dest_integrity"] = _integrity_check(dst)
                if db_name == "knowledge.db":
                    entry["dest_counts"] = {
                        "nodes": _count_tables(dst, "nodes"),
                        "relationships": _count_tables(dst, "relationships"),
                        "sources": _count_tables(dst, "sources"),
                    }
            except Exception as e:
                entry["dest_error"] = str(e)
        out["dbs"][db_name] = entry
    return out


def migrate_project_databases(project_id, source_root=None, data_root=None, create_project=True):
    """Migrate all legacy databases for `project_id` into per-project dir.

    - Creates project directory (and registry) if `create_project` True.
    - For each db in _LEGACY_DBS, if legacy file exists and dest does not, copy via backup.
    - If dest already exists with same SHA, skip (idempotent).
    - If dest exists with different SHA, fail closed for that db (error entry).

    Returns dict: {"ok": bool (all succeeded or skipped), "results": {db_name: migrate_database result or {"ok": True, "skipped": False, "note": "no source"}}}
    """
    if source_root is None:
        source_root = _LEGACY_DIR
    # Validate project_id early
    from ai_engine.paths import _validate_project_id

    _validate_project_id(project_id)

    # Ensure project registry/dirs if requested
    if create_project:
        from ai_engine.paths import ensure_default_project, create_project as _create_project, get_project_entry

        # Ensure data_root exists
        dr = data_root  # may be None -> paths will resolve
        # Use helpers that respect data_root
        try:
            if project_id == DEFAULT_PROJECT_ID:
                ensure_default_project(dr)
            else:
                # ensure default first, then create
                ensure_default_project(dr)
                if get_project_entry(project_id, dr) is None:
                    _create_project(project_id, data_root=dr)
        except ValueError as e:
            # Collision is ok if it is default already present; otherwise propagate
            # If collision error is for the project we are migrating, it means it already exists
            # which is not an error for migration; we still copy DBs.
            # So we only fail if error is not "already exists"
            if "already exists" not in str(e):
                raise
        # Ensure dirs exist even if registry already present
        proj_dir = get_project_dir(project_id, data_root)
        os.makedirs(proj_dir, exist_ok=True)
        os.makedirs(get_snapshots_dir(project_id, data_root), exist_ok=True)
        os.makedirs(get_backups_dir(project_id, data_root), exist_ok=True)

    results = {}
    overall_ok = True
    for db_name in _LEGACY_DBS:
        src = os.path.join(source_root, db_name)
        if not os.path.exists(src):
            results[db_name] = {"ok": True, "skipped": True, "note": "source not present", "source": src}
            continue
        helper = _DB_HELPERS.get(db_name)
        if helper is None:
            results[db_name] = {"ok": False, "skipped": False, "error": f"no helper for {db_name}", "source": src}
            overall_ok = False
            continue
        dst = helper(project_id, data_root)
        res = migrate_database(src, dst)
        results[db_name] = res
        if not res.get("ok"):
            overall_ok = False

    return {"ok": overall_ok, "project_id": project_id, "source_root": source_root, "data_root": data_root, "results": results}


def create_compat_symlink(project_id, legacy_path=None, data_root=None):
    """Attempt to create a compatibility symlink legacy_path -> per-project db.

    Safety: only creates if legacy_path does NOT exist as a real file.
    If legacy_path exists as a regular file, returns error and does not overwrite.
    If legacy_path is already a correct symlink to dest, returns ok/skipped.

    This helper is explicit/opt-in; Phase 0 migration does NOT auto-create symlinks.
    Returns dict {ok, skipped, legacy, dest, error}.
    """
    if legacy_path is None:
        # Default: knowledge.db legacy
        legacy_path = os.path.join(_LEGACY_DIR, "knowledge.db")
    helper = get_knowledge_db
    try:
        dest = helper(project_id, data_root)
    except Exception as e:
        return {"ok": False, "skipped": False, "legacy": legacy_path, "dest": None, "error": str(e)}

    if os.path.exists(legacy_path) and not os.path.islink(legacy_path):
        return {"ok": False, "skipped": False, "legacy": legacy_path, "dest": dest, "error": "legacy exists as real file; refusing to overwrite with symlink"}
    if os.path.islink(legacy_path):
        current = os.readlink(legacy_path)
        # Resolve both to absolute for comparison
        abs_current = os.path.abspath(os.path.join(os.path.dirname(legacy_path), current))
        abs_dest = os.path.abspath(dest)
        if abs_current == abs_dest:
            return {"ok": True, "skipped": True, "legacy": legacy_path, "dest": dest, "note": "already correct symlink"}
        return {"ok": False, "skipped": False, "legacy": legacy_path, "dest": dest, "error": f"legacy is symlink to different target {current!r}"}

    # Legacy does not exist: create symlink
    try:
        os.makedirs(os.path.dirname(legacy_path) or ".", exist_ok=True)
        os.symlink(os.path.abspath(dest), legacy_path)
        return {"ok": True, "skipped": False, "legacy": legacy_path, "dest": dest}
    except Exception as e:
        return {"ok": False, "skipped": False, "legacy": legacy_path, "dest": dest, "error": str(e)}
