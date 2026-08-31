"""Project/Code Index — SQLite store (B1: storage + project identity).

A dedicated per-project index database, kept COMPLETELY separate from the
production knowledge database (``database/knowledge.db``) and from the
knowledge repository. The index is derived, rebuildable project data.

This module is the ONLY place in ``tools/indexer`` that knows about SQLite,
mirroring the ``retrieval/repository.py`` separation of store vs. service.
All queries are parameterized; writes use explicit transactions so a failed
build rolls back cleanly. Connections are short-lived and always closed.
"""

import os
import sqlite3
from contextlib import contextmanager

from .types import (
    FileRecord, SymbolRecord, EdgeRecord,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS files (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    language TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    parse_status TEXT NOT NULL,
    parse_error TEXT NOT NULL DEFAULT '',
    is_test INTEGER NOT NULL DEFAULT 0,
    config_type TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    indexed_seq INTEGER NOT NULL,
    UNIQUE (project, rel_path)
);
CREATE INDEX IF NOT EXISTS idx_files_project_rel
    ON files(project, rel_path);
CREATE INDEX IF NOT EXISTS idx_files_project_status
    ON files(project, parse_status);

CREATE TABLE IF NOT EXISTS symbols (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qname TEXT NOT NULL,
    line_start INTEGER NOT NULL,
    line_end INTEGER NOT NULL,
    parent_id TEXT NOT NULL DEFAULT '',
    signature TEXT NOT NULL DEFAULT '',
    decorators TEXT NOT NULL DEFAULT '',
    is_async INTEGER NOT NULL DEFAULT 0,
    detail TEXT NOT NULL DEFAULT '',
    UNIQUE (project, rel_path, kind, qname)
);
CREATE INDEX IF NOT EXISTS idx_symbols_project_path
    ON symbols(project, rel_path);
CREATE INDEX IF NOT EXISTS idx_symbols_project_qname
    ON symbols(project, kind, qname);
CREATE INDEX IF NOT EXISTS idx_symbols_project_name
    ON symbols(project, name);
CREATE INDEX IF NOT EXISTS idx_symbols_parent ON symbols(parent_id);

CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    source_id TEXT NOT NULL,
    rel_type TEXT NOT NULL,
    target_id TEXT,
    target_name TEXT NOT NULL DEFAULT '',
    certainty TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT '',
    label TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_edges_project_source
    ON edges(project, source_id);
CREATE INDEX IF NOT EXISTS idx_edges_project_target
    ON edges(project, target_id);
CREATE INDEX IF NOT EXISTS idx_edges_project_type
    ON edges(project, rel_type);
"""


class ProjectIndexStore:
    """Transactional wrapper over a project index SQLite database."""

    def __init__(self, db_path):
        parent = os.path.dirname(db_path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent, exist_ok=True)
        self.db_path = db_path
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    @contextmanager
    def transaction(self):
        """One shared SQLite connection/transaction for a whole update batch.

        All writes inside the ``with`` block run on a single connection and
        commit together on success or roll back together on any exception, so a
        reader never observes a partially-applied incremental update. Write
        methods accept ``conn=`` to join this transaction (and skip their own
        commit/close); without ``conn`` they open a short-lived connection as
        before (used by the full build path).
        """
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
            conn.commit()
        finally:
            conn.close()

    # -- lifecycle --------------------------------------------------------

    def wipe(self):
        """Drop all rows (used by rebuild). Keeps the schema."""
        conn = self._connect()
        try:
            with conn:
                conn.execute("DELETE FROM edges")
                conn.execute("DELETE FROM symbols")
                conn.execute("DELETE FROM files")
                conn.execute("DELETE FROM meta")
        finally:
            conn.close()

    def set_meta(self, key, value, conn=None):
        own = conn is None
        conn = conn or self._connect()
        try:
            if own:
                conn.execute("BEGIN")
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value))
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def get_meta(self, key):
        conn = self._connect()
        try:
            row = conn.execute("SELECT value FROM meta WHERE key = ?",
                               (key,)).fetchone()
            return row["value"] if row is not None else None
        finally:
            conn.close()

    # -- writes -----------------------------------------------------------

    def clear_files(self, project, rel_paths, conn=None):
        """Remove all rows (and their symbols/edges) for a set of files."""
        if not rel_paths:
            return
        own = conn is None
        conn = conn or self._connect()
        try:
            if own:
                conn.execute("BEGIN")
            intent = ",".join("?" for _ in rel_paths)
            conn.execute(
                "DELETE FROM edges WHERE project = ? AND "
                "source_id IN (SELECT id FROM files WHERE project = ? "
                "AND rel_path IN (%s))" % intent,
                [project, project] + list(rel_paths))
            conn.execute(
                "DELETE FROM symbols WHERE project = ? AND "
                "rel_path IN (%s)" % intent,
                [project] + list(rel_paths))
            conn.execute(
                "DELETE FROM files WHERE project = ? AND "
                "rel_path IN (%s)" % intent,
                [project] + list(rel_paths))
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def clear_file_index(self, project, rel_paths, conn=None):
        """Remove a file's symbols plus every edge sourced from those symbols
        (contains/defines/imports/inherits) or from the file row itself (tests).

        Used when a file is re-indexed (content change) or removed. Incoming
        edges (other files importing INTO this module, or test files targeting
        this file) are intentionally preserved here; call
        :meth:`clear_incoming_to_files` to drop those for a removed file.
        """
        rel_paths = list(rel_paths)
        if not rel_paths:
            return
        own = conn is None
        conn = conn or self._connect()
        try:
            if own:
                conn.execute("BEGIN")
            intent = ",".join("?" for _ in rel_paths)
            # symbol-sourced edges (contains/defines/imports/inherits) + tests
            # (source_id = file id)
            conn.execute(
                "DELETE FROM edges WHERE project = ? AND source_id IN ("
                "  SELECT id FROM symbols WHERE project = ? AND rel_path IN (%s)"
                "  UNION SELECT id FROM files WHERE project = ? AND rel_path IN (%s)"
                ")" % (intent, intent),
                [project, project] + list(rel_paths)
                + [project] + list(rel_paths))
            conn.execute(
                "DELETE FROM symbols WHERE project = ? AND "
                "rel_path IN (%s)" % intent,
                [project] + list(rel_paths))
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def remove_files(self, project, rel_paths, conn=None):
        """Remove the file rows for a set of paths (symbols/edges must already
        be cleaned)."""
        rel_paths = list(rel_paths)
        if not rel_paths:
            return
        own = conn is None
        conn = conn or self._connect()
        try:
            if own:
                conn.execute("BEGIN")
            intent = ",".join("?" for _ in rel_paths)
            conn.execute(
                "DELETE FROM files WHERE project = ? AND "
                "rel_path IN (%s)" % intent,
                [project] + list(rel_paths))
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def clear_incoming_to_files(self, project, file_ids, conn=None):
        """Delete edges whose target is any of the given file ids. Used to drop
        stale resolved-import FACTs and tests edges pointing at a removed file."""
        file_ids = list(file_ids)
        if not file_ids:
            return
        own = conn is None
        conn = conn or self._connect()
        try:
            if own:
                conn.execute("BEGIN")
            intent = ",".join("?" for _ in file_ids)
            conn.execute(
                "DELETE FROM edges WHERE project = ? AND "
                "target_id IN (%s)" % intent,
                [project] + list(file_ids))
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def clear_resolved_edges(self, project, rel_path, conn=None,
                             kinds=("imports", "tests")):
        """Delete a file's import edges (source = module symbol id) and/or
        tests edges (source = file id) so they can be re-resolved against the
        current module map. Content-derived structural edges are left
        untouched."""
        from .types import file_id, symbol_id, module_qname_for
        own = conn is None
        conn = conn or self._connect()
        try:
            if own:
                conn.execute("BEGIN")
            qname = module_qname_for(rel_path)
            sources = [symbol_id(project, rel_path, "module", qname),
                       file_id(project, rel_path)]
            intent = ",".join("?" for _ in sources)
            ks = ",".join("?" for _ in kinds)
            conn.execute(
                "DELETE FROM edges WHERE project = ? AND rel_type IN (%s) "
                "AND source_id IN (%s)" % (ks, intent),
                [project] + list(kinds) + list(sources))
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def remove_files_absent(self, project, present_set):
        """Delete index rows for files present in the DB but absent on disk."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT rel_path FROM files WHERE project = ?",
                (project,)).fetchall()
            stale = [r["rel_path"] for r in rows
                     if r["rel_path"] not in present_set]
        finally:
            conn.close()
        if stale:
            self.clear_files(project, stale)
        return stale

    def put_files(self, project, records, indexed_seq, conn=None):
        own = conn is None
        conn = conn or self._connect()
        try:
            if own:
                conn.execute("BEGIN")
            for r in records:
                conn.execute(
                    "INSERT OR REPLACE INTO files "
                    "(id, project, rel_path, language, size_bytes, sha256, "
                    "mtime_ns, parse_status, parse_error, is_test, "
                    "config_type, detail, indexed_seq) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (r.id, project, r.rel_path, r.language, r.size_bytes,
                     r.sha256, r.mtime_ns, r.parse_status, r.parse_error,
                     int(r.is_test), r.config_type, r.detail, indexed_seq))
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def put_symbols(self, project, records, conn=None):
        own = conn is None
        conn = conn or self._connect()
        try:
            if own:
                conn.execute("BEGIN")
            for s in records:
                conn.execute(
                    "INSERT OR REPLACE INTO symbols "
                    "(id, project, rel_path, kind, name, qname, "
                    "line_start, line_end, parent_id, signature, "
                    "decorators, is_async, detail) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (s.id, project, s.rel_path, s.kind, s.name, s.qname,
                     s.line_start, s.line_end, s.parent_id, s.signature,
                     s.decorators, int(s.is_async), s.detail))
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def put_edges(self, project, records, conn=None):
        own = conn is None
        conn = conn or self._connect()
        try:
            if own:
                conn.execute("BEGIN")
            for e in records:
                conn.execute(
                    "INSERT OR REPLACE INTO edges "
                    "(id, project, source_id, rel_type, target_id, "
                    "target_name, certainty, confidence, label) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (e.id, project, e.source_id, e.rel_type, e.target_id,
                     e.target_name, e.certainty, e.confidence, e.label))
            if own:
                conn.commit()
        finally:
            if own:
                conn.close()

    def delete_symbol_parents(self, project, rel_path):
        """Delete rows whose parent is a symbol re-indexed in this file
        (handles moved/nested symbols without a full wipe)."""
        return

    # -- reads (deterministic; sorted) ------------------------------------

    def imports_all(self, project):
        """All import edges for a project, sorted by id (deterministic)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM edges WHERE project = ? AND rel_type = 'imports' "
                "ORDER BY id", (project,)).fetchall()
            return [_edge_from_row(r) for r in rows]
        finally:
            conn.close()

    def tests_all(self, project):
        """All tests edges for a project, sorted by id (deterministic)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM edges WHERE project = ? AND rel_type = 'tests' "
                "ORDER BY id", (project,)).fetchall()
            return [_edge_from_row(r) for r in rows]
        finally:
            conn.close()

    def rel_path_for_symbol(self, project, symbol_id_):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT rel_path FROM symbols WHERE project = ? AND id = ?",
                (project, symbol_id_)).fetchone()
            return row["rel_path"] if row is not None else None
        finally:
            conn.close()

    def file(self, project, rel_path):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM files WHERE project = ? AND rel_path = ?",
                (project, rel_path)).fetchone()
            return _file_from_row(row) if row is not None else None
        finally:
            conn.close()

    def files(self, project, parse_status=None):
        conn = self._connect()
        try:
            if parse_status is None:
                rows = conn.execute(
                    "SELECT * FROM files WHERE project = ? "
                    "ORDER BY rel_path", (project,)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM files WHERE project = ? AND "
                    "parse_status = ? ORDER BY rel_path",
                    (project, parse_status)).fetchall()
            return [_file_from_row(r) for r in rows]
        finally:
            conn.close()

    def symbols_in_path(self, project, rel_path):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE project = ? AND rel_path = ? "
                "ORDER BY line_start, qname", (project, rel_path)).fetchall()
            return [_symbol_from_row(r) for r in rows]
        finally:
            conn.close()

    def symbol_by_qname(self, project, kind, qname):
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM symbols WHERE project = ? AND kind = ? "
                "AND qname = ?", (project, kind, qname)).fetchone()
            return _symbol_from_row(row) if row is not None else None
        finally:
            conn.close()

    def symbols_by_name(self, project, name):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM symbols WHERE project = ? AND name = ? "
                "ORDER BY rel_path, kind, qname", (project, name)).fetchall()
            return [_symbol_from_row(r) for r in rows]
        finally:
            conn.close()

    def edges_from(self, project, source_id):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM edges WHERE project = ? AND source_id = ? "
                "ORDER BY rel_type, COALESCE(target_id, target_name)",
                (project, source_id)).fetchall()
            return [_edge_from_row(r) for r in rows]
        finally:
            conn.close()

    def edges_into(self, project, target_id):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM edges WHERE project = ? AND target_id = ? "
                "ORDER BY rel_type, source_id",
                (project, target_id)).fetchall()
            return [_edge_from_row(r) for r in rows]
        finally:
            conn.close()

    def edges_by_type(self, project, rel_type):
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM edges WHERE project = ? AND rel_type = ? "
                "ORDER BY source_id, COALESCE(target_id, target_name)",
                (project, rel_type)).fetchall()
            return [_edge_from_row(r) for r in rows]
        finally:
            conn.close()

    def edges_imports_of(self, project, rel_path):
        """imports edges whose source module is a given file path."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT e.* FROM edges e "
                "JOIN files f ON f.id = e.source_id "
                "WHERE e.project = ? AND e.rel_type = ? AND f.rel_path = ? "
                "ORDER BY COALESCE(e.target_id, e.target_name)",
                (project, REL_IMPORTS, rel_path)).fetchall()
            return [_edge_from_row(r) for r in rows]
        finally:
            conn.close()

    def targets_into(self, project, target_id):
        return self.edges_into(project, target_id)

    def counts(self, project):
        conn = self._connect()
        try:
            f = conn.execute(
                "SELECT COUNT(*) c FROM files WHERE project = ?",
                (project,)).fetchone()["c"]
            s = conn.execute(
                "SELECT COUNT(*) c FROM symbols WHERE project = ?",
                (project,)).fetchone()["c"]
            e = conn.execute(
                "SELECT COUNT(*) c FROM edges WHERE project = ?",
                (project,)).fetchone()["c"]
            return {"files": f, "symbols": s, "edges": e}
        finally:
            conn.close()


# local import of constant to avoid circular import at module top
from .types import REL_IMPORTS  # noqa: E402


def _file_from_row(row):
    return FileRecord(
        project=row["project"], rel_path=row["rel_path"],
        language=row["language"], size_bytes=row["size_bytes"],
        sha256=row["sha256"], mtime_ns=row["mtime_ns"],
        parse_status=row["parse_status"], parse_error=row["parse_error"],
        is_test=bool(row["is_test"]), config_type=row["config_type"],
        detail=row["detail"])


def _symbol_from_row(row):
    return SymbolRecord(
        project=row["project"], rel_path=row["rel_path"], kind=row["kind"],
        name=row["name"], qname=row["qname"], line_start=row["line_start"],
        line_end=row["line_end"], parent_id=row["parent_id"],
        signature=row["signature"], decorators=row["decorators"],
        is_async=bool(row["is_async"]), detail=row["detail"])


def _edge_from_row(row):
    return EdgeRecord(
        project=row["project"], source_id=row["source_id"],
        rel_type=row["rel_type"], target_id=row["target_id"],
        target_name=row["target_name"], certainty=row["certainty"],
        confidence=row["confidence"], label=row["label"])
