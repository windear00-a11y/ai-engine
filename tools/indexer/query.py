"""Project/Code Index — deterministic query API (B6).

Read-only queries over an indexed project. Every result is sorted and
deterministic. Queries never raise; they return empty/structured results.

This is an INTERNAL API for the future Planner (Component C). It is NOT part of
Contract v1 and does not touch the knowledge system.
"""

from .store import ProjectIndexStore
from .types import (
    module_qname_for,
    REL_IMPORTS, REL_CALLS, REL_INHERITS, REL_TESTS,
    CERTAINTY_FACT,
)


class IndexQueries:
    """Deterministic, read-only access to a built project index."""

    def __init__(self, db_path):
        self.store = ProjectIndexStore(db_path)
        self.project = self.store.get_meta("project_id")

    # -- files ------------------------------------------------------------

    def find_file(self, rel_path):
        """Return the FileRecord dict for a rel_path, or None."""
        if self.project is None:
            return None
        rec = self.store.file(self.project, rel_path)
        return rec.as_dict() if rec is not None else None

    def file_manifest(self, parse_status=None):
        """All files, sorted by rel_path. Optionally filtered by status."""
        if self.project is None:
            return []
        recs = self.store.files(self.project, parse_status=parse_status)
        return [r.as_dict() for r in recs]

    def files_by_language(self, language):
        if self.project is None:
            return []
        return [r.as_dict() for r in self.store.files(self.project)
                if r.language == language]

    # -- symbols ----------------------------------------------------------

    def symbols_in_file(self, rel_path):
        """All structural symbols in a file, sorted by line then qname."""
        if self.project is None:
            return []
        return [s.as_dict()
                for s in self.store.symbols_in_path(self.project, rel_path)]

    def find_symbol(self, qname, kind=None):
        """Locate a symbol by its dotted qualified name. Returns list (there
        may be sibling modules with the same qname; caller filters by rel_path
        if needed)."""
        if self.project is None:
            return []
        if kind is not None:
            rec = self.store.symbol_by_qname(self.project, kind, qname)
            return rec.as_dict() if rec is not None else None
        recs = []
        for k in ("module", "class", "function", "method"):
            s = self.store.symbol_by_qname(self.project, k, qname)
            if s is not None:
                recs.append(s.as_dict())
        return recs

    def find_symbols_by_name(self, name):
        """All symbols whose short name matches, sorted deterministically."""
        if self.project is None:
            return []
        return [s.as_dict() for s in self.store.symbols_by_name(
            self.project, name)]

    def module_symbol(self, rel_path):
        """The module symbol record for a python file, or None."""
        if self.project is None:
            return None
        qname = self._module_qname(rel_path)
        return self.store.symbol_by_qname(self.project, "module", qname)

    # -- relationships ----------------------------------------------------

    def imports_of(self, rel_path):
        """imports edges (resolved facts + unresolved candidates) of a file."""
        if self.project is None:
            return []
        mod = self.module_symbol(rel_path)
        if mod is None:
            return []
        return [e.as_dict() for e in self.store.edges_from(
            self.project, mod.id) if e.rel_type == REL_IMPORTS]

    def dependents_of(self, module_qname):
        """Files that import a given module (resolved fact edges)."""
        if self.project is None:
            return []
        out = []
        for fr in self.store.files(self.project):
            if fr.language != "python":
                continue
            mod = self.store.symbol_by_qname(self.project, "module",
                                             module_qname_for(fr.rel_path))
            if mod is None:
                continue
            for e in self.store.edges_from(self.project, mod.id):
                if e.rel_type != REL_IMPORTS or \
                   e.certainty != CERTAINTY_FACT:
                    continue
                # target_id is the imported file id; map to its module qname
                target_file = self._file_by_id(e.target_id or "")
                if target_file is not None and \
                   module_qname_for(target_file.rel_path) == module_qname:
                    out.append(fr.rel_path)
        return sorted(set(out))

    def inherited_bases(self, rel_path, qname):
        """inherits edges for a class symbol."""
        if self.project is None:
            return []
        cls = self.store.symbol_by_qname(self.project, "class", qname)
        if cls is None:
            return []
        return [e.as_dict() for e in self.store.edges_from(
            self.project, cls.id) if e.rel_type == REL_INHERITS]

    def tests_for(self, rel_path):
        """Test files that conservatively cover a source file (fact edges)."""
        if self.project is None:
            return []
        src = self.store.file(self.project, rel_path)
        if src is None:
            return []
        out = []
        for e in self.store.edges_into(self.project, src.id):
            if e.rel_type == REL_TESTS:
                out.append(e.source_id)
        # map source ids back to rel_paths
        result = []
        for source_id in out:
            fr = self._file_by_id(source_id)
            if fr is not None:
                result.append(fr.rel_path)
        return sorted(set(result))

    def find_references(self, qname):
        """All edges that reference a symbol or module by qname (as target).

        Three independent, non-overlapping matches are combined so that both
        resolved facts and unresolved candidates surface in one deterministic
        result set:

        * symbol ids whose qname matches (structural/edge targets like calls);
        * resolved import facts, which reference the *file* id of the imported
          module (not a symbol id) — matched via each python file whose module
          qname equals ``qname``;
        * unresolved ``target_name`` edges, whose target_name is the dotted
          module qname of the not-yet-indexed module.

        Nothing is fabricated: each returned edge genuinely references the
        queried qname in the stored model.
        """
        if self.project is None:
            return []
        out = {}

        # 1) symbol ids whose qname matches, across every kind and path
        target_ids = set()
        for fr in self.store.files(self.project):
            if fr.language != "python":
                continue
            for sym in self.store.symbols_in_path(self.project, fr.rel_path):
                if sym.qname == qname:
                    target_ids.add(sym.id)
        for tid in target_ids:
            for e in self.store.edges_into(self.project, tid):
                out[e.id] = e

        # 2) resolved module-import facts reference the file id, so match the
        #    file ids whose module qname equals the queried qname. Restrict to
        #    import facts so test/other edges into a file are not misreported.
        for fr in self.store.files(self.project):
            if fr.language != "python":
                continue
            if module_qname_for(fr.rel_path) != qname:
                continue
            for e in self.store.edges_into(self.project, fr.id):
                if e.rel_type == REL_IMPORTS:
                    out[e.id] = e

        # 3) unresolved target_name edges across the project
        for fr in self.store.files(self.project):
            if fr.language != "python":
                continue
            mod = self.store.symbol_by_qname(self.project, "module",
                                             module_qname_for(fr.rel_path))
            if mod is None:
                continue
            for e in self.store.edges_from(self.project, mod.id):
                if e.rel_type in (REL_IMPORTS, REL_INHERITS, REL_CALLS) and \
                   e.target_name == qname:
                    out[e.id] = e
        return [e.as_dict() for e in sorted(out.values(),
                                            key=lambda e: e.id)]

    def find_callers(self, qname):
        return [e for e in self.find_references(qname)
                if e["rel_type"] == REL_CALLS]

    def find_callees(self, qname):
        if self.project is None:
            return []
        sym = self.store.symbol_by_qname(self.project, "class", qname) or \
            self.store.symbol_by_qname(self.project, "function", qname) or \
            self.store.symbol_by_qname(self.project, "method", qname)
        if sym is None:
            return []
        return [e.as_dict() for e in self.store.edges_from(
            self.project, sym.id)]

    # -- project ----------------------------------------------------------

    def project_info(self):
        return {
            "project": self.store.get_meta("project_id"),
            "root": self.store.get_meta("root"),
            "index_version": self.store.get_meta("index_version"),
            "counts": self.store.counts(self.project),
        }

    # -- internals --------------------------------------------------------

    def _module_qname(self, rel_path):
        return module_qname_for(rel_path)

    def _file_by_id(self, file_id_val):
        for fr in self.store.files(self.project):
            if fr.id == file_id_val:
                return fr
        return None
