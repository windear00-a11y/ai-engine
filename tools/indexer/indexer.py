"""Project/Code Index — lifecycle facade (build / rebuild / inspect).

Coordinates discovery (B2), Python AST extraction (B3) and the SQLite store
(B1) into a deterministic, all-or-nothing build. This is the only entry point
that ties collection together; relationship resolution is deliberately
conservative (facts only when reliably established).

This module is NOT wired into TaskEngine/CodingTools yet (per approved scope).
"""

import io
import os

from . import languages, discovery, python_ast
from .store import ProjectIndexStore
from .types import (
    EdgeRecord, project_id, file_id, symbol_id, module_qname_for,
    STATUS_OK, STATUS_UNSUPPORTED,
    REL_IMPORTS, REL_TESTS, CERTAINTY_FACT, CERTAINTY_CANDIDATE,
)

INDEX_VERSION = "1"
INDEX_DIR_NAME = ".ai-engine"


def default_db_path(workspace_root):
    """<workspace_root>/.ai-engine/project_index.db (per approved location)."""
    return os.path.join(workspace_root, INDEX_DIR_NAME, "project_index.db")


class ProjectIndex:
    """Facade over a single project's index."""

    def __init__(self, workspace_root, db_path=None):
        self.root = os.path.realpath(workspace_root)
        self.project = project_id(self.root)
        self.db_path = db_path or default_db_path(self.root)
        self.store = ProjectIndexStore(self.db_path)

    # -- lifecycle --------------------------------------------------------

    def build(self, source_bytes_reader=None):
        """Full, deterministic build from the current workspace on disk.

        Wipes the index first (rebuild semantics), then walks the workspace.
        Returns a summary dict. Errors never abort the build (per-file
        isolation); a summary of failed files is included.
        """
        self.store.wipe()
        self.store.set_meta("project_id", self.project)
        self.store.set_meta("root", self.root)
        self.store.set_meta("index_version", INDEX_VERSION)
        self.store.set_meta("kind", "full")

        ignore = discovery.build_ignore(self.root)
        file_records = discovery.discover(self.root, self.project,
                                          ignore=ignore)
        indexed_seq = 1
        self.store.put_files(self.project, file_records, indexed_seq)

        # extract structural symbols + edges per python file
        structural_edges = []
        symbols_by_path = {}
        extracted_files = []
        for fr in file_records:
            if fr.parse_status == STATUS_OK and \
               fr.language == languages.LANG_PYTHON:
                records, edges = self._extract_python(fr)
                # extraction may reclassify a file (syntax_error, decode_error,
                # parse_aborted, unreadable); keep it to persist the final
                # parse_status/parse_error back to the files table.
                extracted_files.append(fr)
                self.store.put_symbols(self.project, records if records else [])
                for e in edges:
                    if e.rel_type in (python_ast.REL_CONTAINS,
                                      python_ast.REL_DEFINES,
                                      python_ast.REL_INHERITS):
                        structural_edges.append(e)
                for r in records:
                    symbols_by_path.setdefault(fr.rel_path, set()).add(r.qname)
            elif fr.language in languages.STRUCTURAL_LANGUAGES and \
                    fr.parse_status != STATUS_UNSUPPORTED:
                # non-python structural; nothing here yet
                pass

        # persist post-extraction file status: extraction may have flipped a
        # file from 'ok' to syntax_error/decode_error/parse_aborted/unreadable,
        # so the files table must reflect that (idempotent by project+rel_path).
        if extracted_files:
            self.store.put_files(self.project, extracted_files, indexed_seq)

        # resolved import edges + conservative test associations
        resolved_edges = self._collect_edges(file_records, symbols_by_path)

        all_edges = structural_edges + resolved_edges
        self.store.put_edges(self.project, all_edges)

        counts = self.store.counts(self.project)
        failed = [fr.rel_path for fr in file_records
                  if fr.parse_status not in (STATUS_OK, STATUS_UNSUPPORTED)]
        counts["errors"] = failed
        return {
            "project": self.project,
            "root": self.root,
            "db_path": self.db_path,
            "counts": {"files": counts["files"], "symbols": counts["symbols"],
                       "edges": counts["edges"]},
            "errors": failed,
        }

    def rebuild(self):
        """Alias for build(): a full, deterministic rebuild is always correct."""
        return self.build()

    def incremental_update(self):
        """Apply only the changes between the stored index and the current
        tree, in a single shared transaction.

        Content checksums (sha256) are authoritative; size+mtime are hints.
        Files whose checksum is unchanged are skipped entirely (no re-parse,
        no DB churn). When the module map changes (a module deleted, renamed,
        or its parse status flipped), only the direct dependents of those
        modules are re-resolved, never the whole tree.

        Logical convergence: running incremental_update() over any sequence of
        states yields an index logically identical to a clean rebuild() of the
        final state.
        """
        ignore = discovery.build_ignore(self.root)
        new_records = discovery.discover(self.root, self.project,
                                         ignore=ignore)
        new_by_path = {fr.rel_path: fr for fr in new_records}

        stored = self.store.files(self.project)
        stored_by_path = {fr.rel_path: fr for fr in stored}

        changed = []   # modified or new: fully re-index
        deleted = []   # removed from disk
        hint_only = []  # content unchanged: refresh mtime/size hint only

        for path, new_rec in new_by_path.items():
            old = stored_by_path.get(path)
            if old is None:
                changed.append(path)
            elif old.sha256 != new_rec.sha256:
                changed.append(path)
            else:
                hint_only.append(path)

        for path in stored_by_path:
            if path not in new_by_path:
                deleted.append(path)

        # Old (pre-update) module map, for detecting qname-availability flips.
        old_ok_qnames = {
            self._file_module_qname(fr.rel_path)
            for fr in stored
            if fr.language == languages.LANG_PYTHON and
            fr.parse_status == STATUS_OK
        }

        with self.store.transaction() as conn:
            self.store.set_meta("kind", "incremental", conn=conn)

            # ---- handle deletes first (they affect the module map) --------
            for path in deleted:
                self.store.clear_file_index(self.project, [path], conn=conn)

            # Drop incoming edges pointing at removed file ids.
            removed_file_ids = [
                stored_by_path[p].id for p in deleted if p in stored_by_path]
            self.store.clear_incoming_to_files(
                self.project, removed_file_ids, conn=conn)
            self.store.remove_files(self.project, deleted, conn=conn)

            # ---- re-index changed/new files --------------------------------
            for path in sorted(changed):
                new_rec = new_by_path[path]
                self.store.clear_file_index(self.project, [path], conn=conn)
                symbols, structural = self._extract_python(new_rec)
                self.store.put_symbols(self.project, symbols, conn=conn)
                self.store.put_edges(self.project, structural, conn=conn)
                self.store.put_files(self.project, [new_rec], 1, conn=conn)

            # ---- refresh hints for unchanged files ------------------------
            if hint_only:
                self.store.put_files(
                    self.project,
                    [new_by_path[p] for p in sorted(hint_only)],
                    1, conn=conn)

            # ---- recompute the module map from an IN-MEMORY post-update
            #      view (a separate reader connection cannot see writes still
            #      inside the open transaction, so we must not re-query here).
            cur_records = list(stored)
            for p in deleted:
                cur_records = [fr for fr in cur_records if fr.rel_path != p]
            for p in sorted(changed + hint_only):
                rec = new_by_path[p]
                cur_records = [fr for fr in cur_records
                               if fr.rel_path != p] + [rec]
            current_by_path = {fr.rel_path: fr for fr in cur_records}
            new_ok_qnames = {
                self._file_module_qname(fr.rel_path)
                for fr in cur_records
                if fr.language == languages.LANG_PYTHON and
                fr.parse_status == STATUS_OK
            }
            qname_to_path = {}
            for fr in cur_records:
                if fr.language == languages.LANG_PYTHON and \
                   fr.parse_status == STATUS_OK:
                    qname_to_path.setdefault(
                        self._file_module_qname(fr.rel_path), fr.rel_path)

            # ---- determine which paths need edge re-resolution ------------
            importer_index = self._importer_index(stored)
            unresolved_by_qname = self._unresolved_importers_by_qname()

            affected = set(changed)
            # FACT dependents of every module whose qname LEAVES the module map
            # (deleted, renamed, or its parse status flipped to non-ok) must
            # re-resolve, because their resolved import may no longer exist.
            old_by_qname = {}
            for fr in stored:
                if fr.language == languages.LANG_PYTHON and \
                   fr.parse_status == STATUS_OK:
                    old_by_qname.setdefault(
                        self._file_module_qname(fr.rel_path), fr.rel_path)
            leaving_qnames = old_ok_qnames - new_ok_qnames
            for qn in leaving_qnames:
                old_path = old_by_qname.get(qn)
                if old_path:
                    affected |= importer_index.get(old_path, set())
            # unresolved candidates whose target qname just became available
            # must re-resolve to a FACT when possible.
            newly_available = new_ok_qnames - old_ok_qnames
            for qn in newly_available:
                affected |= unresolved_by_qname.get(qn, set())

            # ---- pass 1: re-resolve IMPORTS for affected paths ------------
            for path in sorted(affected):
                fr = current_by_path.get(path)
                if fr is None or fr.language != languages.LANG_PYTHON or \
                   fr.parse_status != STATUS_OK:
                    continue
                self.store.clear_resolved_edges(
                    self.project, path, conn=conn, kinds=("imports",))
                self.store.put_edges(
                    self.project, self._resolve_imports_for(fr, qname_to_path),
                    conn=conn)

            # ---- pass 2: re-resolve TESTS for every current test file -----
            # Test associations depend on the whole module map and are cheap
            # (metadata only), so always recompute them for consistency with a
            # full rebuild.
            for fr in cur_records:
                if not fr.is_test or \
                   fr.language != languages.LANG_PYTHON or \
                   fr.parse_status != STATUS_OK:
                    continue
                self.store.clear_resolved_edges(
                    self.project, fr.rel_path, conn=conn, kinds=("tests",))
                self.store.put_edges(
                    self.project, self._tests_edges_for(fr, qname_to_path),
                    conn=conn)

        counts = self.store.counts(self.project)
        failed = [fr.rel_path for fr in new_records
                  if fr.parse_status not in (STATUS_OK, STATUS_UNSUPPORTED)]
        return {
            "project": self.project,
            "root": self.root,
            "db_path": self.db_path,
            "counts": {"files": counts["files"], "symbols": counts["symbols"],
                       "edges": counts["edges"]},
            "changes": {
                "added": sorted(p for p in changed if p not in stored_by_path),
                "modified": sorted(
                    p for p in changed if p in stored_by_path),
                "deleted": sorted(deleted),
                "unchanged_skipped": sorted(hint_only),
            },
            "errors": failed,
        }

    def _importer_index(self, current_files):
        """Map target rel_path -> set of importing module rel_paths, built from
        stored resolved-import FACT edges. Also maps file_id -> rel_path for
        reverse lookup. Used to find direct dependents of removed modules."""
        id_to_path = {fr.id: fr.rel_path for fr in current_files}
        importers_by_target = {}
        for edge in self.store.imports_all(self.project):
            if edge.certainty != CERTAINTY_FACT or not edge.target_id:
                continue
            tgt_path = id_to_path.get(edge.target_id)
            if not tgt_path:
                continue
            src_module = self.store.rel_path_for_symbol(
                self.project, edge.source_id)
            if src_module:
                importers_by_target.setdefault(tgt_path, set()).add(src_module)
        return importers_by_target

    def _unresolved_importers_by_qname(self):
        """Map unresolved target_qname -> set of importer rel_paths from stored
        candidate import edges (so a newly-available qname can flip them)."""
        out = {}
        for edge in self.store.imports_all(self.project):
            if edge.certainty == CERTAINTY_FACT or not edge.target_name:
                continue
            src_module = self.store.rel_path_for_symbol(
                self.project, edge.source_id)
            if src_module:
                out.setdefault(edge.target_name, set()).add(src_module)
        return out

    def inspect(self):
        """Report current status/counts without re-walking."""
        meta = {}
        for key in ("project_id", "root", "index_version", "kind"):
            meta[key] = self.store.get_meta(key)
        meta["counts"] = self.store.counts(self.project)
        return meta

    # -- helpers ----------------------------------------------------------

    def _extract_python(self, file_record):
        abs_path = os.path.join(self.root, file_record.rel_path)
        try:
            with io.open(abs_path, "rb") as f:
                source = f.read()
        except OSError as e:
            file_record.parse_status = "unreadable"
            file_record.parse_error = str(e)
            return [], []
        status, err, symbols, edges, imports = python_ast.extract(
            self.project, file_record.rel_path, source)
        file_record.parse_status = status
        file_record.parse_error = err
        if status != STATUS_OK:
            return [], []
        return symbols, edges

    def _file_module_qname(self, rel_path):
        return module_qname_for(rel_path)

    def _resolve_import(self, rel_path, core_qname, from_mod, name, level):
        """Resolve a (possibly relative) import to a dotted module name.

        ``rel_path`` is the importing file, ``core_qname`` its module qname.
        ``level`` 0 means absolute; >0 is a relative import. Returns the
        candidate dotted module qname (may not exist in the index).

        Relative imports resolve against Python's ``__package__``:
        * a regular module ``pkg.core`` has ``__package__ = pkg`` (drop the
          last qname segment, the module name itself);
        * a package ``pkg/__init__.py`` has ``core_qname == pkg`` and that IS
          the package (keep all segments).
        ``level`` counts leading dots from ``__package__``: ``.`` stays in the
        package, ``..`` ascends one more, etc.
          e.g. pkg.core, level=1, from_mod='utils' -> pkg.utils
          e.g. pkg.core, level=2, from_mod='shared' -> shared
          e.g. pkg/__init__.py, level=1, from_mod='core' -> pkg.core
        """
        parts = core_qname.split(".")
        if level == 0:
            head = from_mod if from_mod else name
            # `import name` -> name itself; `from mod import name` -> mod
            return head
        package = parts if rel_path.endswith("__init__.py") else parts[:-1]
        if level > len(package):
            base = []
        else:
            base = package[:len(package) - (level - 1)]
        if from_mod:
            suffix = from_mod.split(".")
            return ".".join(base + suffix)
        return ".".join(base + [name])

    def _resolve_imports_for(self, fr, qname_to_path):
        """Re-read a python file, resolve its imports against the current
        qname->path map, and return the import edges. Returns [] if the file
        is not a currently-ok python module or cannot be read."""
        if fr.language != languages.LANG_PYTHON or \
           fr.parse_status != STATUS_OK:
            return []
        abs_path = os.path.join(self.root, fr.rel_path)
        try:
            with io.open(abs_path, "rb") as fh:
                source = fh.read()
        except OSError:
            return []
        status, _err, _s, _e, imports = python_ast.extract(
            self.project, fr.rel_path, source)
        if status != STATUS_OK:
            return []
        module_id = symbol_id(self.project, fr.rel_path, "module",
                              self._file_module_qname(fr.rel_path))
        core_qname = self._file_module_qname(fr.rel_path)
        edges = []
        for from_mod, name, alias, level in imports:
            target_mod = self._resolve_import(fr.rel_path, core_qname,
                                              from_mod, name, level)
            resolved = qname_to_path.get(target_mod)
            if resolved:
                tgt_file_id = file_id(self.project, resolved)
                edges.append(EdgeRecord(
                    project=self.project, source_id=module_id,
                    rel_type=REL_IMPORTS, target_id=tgt_file_id,
                    target_name=resolved, certainty=CERTAINTY_FACT,
                    confidence="resolved",
                    label="%s -> %s" % (fr.rel_path, resolved)))
            else:
                edges.append(EdgeRecord(
                    project=self.project, source_id=module_id,
                    rel_type=REL_IMPORTS, target_id="",
                    target_name=target_mod, certainty=CERTAINTY_CANDIDATE,
                    confidence="unresolved",
                    label="%s -> %s" % (fr.rel_path, target_mod)))
        return edges

    def _tests_edges_for(self, fr, qname_to_path):
        """Conservative test -> source associations for one test file."""
        if not fr.is_test or fr.language != languages.LANG_PYTHON or \
           fr.parse_status != STATUS_OK:
            return []
        src_guess = self._tested_module(fr.rel_path)
        edges = []
        if src_guess and src_guess in qname_to_path:
            src_path = qname_to_path[src_guess]
            tgt = file_id(self.project, src_path)
            src = file_id(self.project, fr.rel_path)
            edges.append(EdgeRecord(
                project=self.project, source_id=src, rel_type=REL_TESTS,
                target_id=tgt, target_name=src_path,
                certainty=CERTAINTY_FACT, confidence="high",
                label="%s tests %s" % (fr.rel_path, src_path)))
        elif src_guess:
            for qn, path in qname_to_path.items():
                if qn.split(".")[-1] == src_guess and \
                   self._file_module_qname(fr.rel_path) != qn:
                    tgt = file_id(self.project, path)
                    src = file_id(self.project, fr.rel_path)
                    edges.append(EdgeRecord(
                        project=self.project, source_id=src,
                        rel_type=REL_TESTS, target_id=tgt,
                        target_name=path, certainty=CERTAINTY_FACT,
                        confidence="high",
                        label="%s tests %s" % (fr.rel_path, path)))
                    break
        return edges

    def _collect_edges(self, file_records, symbols_by_path):
        """Resolve imports/inheritance/tests edges deterministically.

        Keeps the raw edges produced during extraction plus adds resolved
        facts and conservative test->source associations. Unresolved names are
        preserved as plain strings (candidate), never fabricated.
        """
        resolved_edges = []

        # Build a module qname -> rel_path map over python files.
        qname_to_path = {}
        for fr in file_records:
            if fr.language == languages.LANG_PYTHON and \
               fr.parse_status == STATUS_OK:
                qname_to_path.setdefault(
                    self._file_module_qname(fr.rel_path), fr.rel_path)

        # resolve imports by re-reading extraction; simplest correct approach:
        for fr in file_records:
            resolved_edges.extend(self._resolve_imports_for(fr, qname_to_path))

        # conservative test -> source associations
        for fr in file_records:
            resolved_edges.extend(self._tests_edges_for(fr, qname_to_path))

        return resolved_edges

    def _tested_module(self, rel_path):
        """Best-guess module a test file covers, or None if not derivable."""
        base = os.path.basename(rel_path)
        guess = None
        if base.startswith("test_"):
            guess = base[len("test_"):]
        elif base.endswith("_test.py"):
            guess = base[:-len("_test.py")]
        if guess is None:
            return None
        if guess.endswith(".py"):
            guess = guess[:-3]
        return guess
