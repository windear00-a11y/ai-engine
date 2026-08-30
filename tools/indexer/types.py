"""Project/Code Index — shared types and deterministic identifiers.

Component B of the original Phase 1 (A: safety, B: project index, C: planner).

Determinism contract: identifiers and every exported ordering are derived ONLY
from stable inputs (project root path, relative paths, symbol kind/qualified
names). No randomness, no timestamps in identity. Re-running over the same
project state produces byte-identical output. Timestamps/mtimes are used ONLY
as change-detection hints, never as identifiers.
"""

import hashlib
from dataclasses import dataclass


def stable_id(*parts):
    """Deterministic 20-char id from arbitrary stable string parts.

    Reuses the same idiom as ``knowledge_compiler.types.stable_id`` (sha256 of
    a canonical join). Kept local so ``tools/indexer`` has no hard dependency
    on the knowledge compiler package. The same parts always yield the same id.
    """
    canonical = "|".join(str(p) for p in parts).encode("utf-8", "replace")
    return hashlib.sha256(canonical).hexdigest()[:20]


# -- identity ---------------------------------------------------------------

def project_id(workspace_root):
    """Deterministic project identifier from the resolved workspace root."""
    return stable_id("project", workspace_root)


def file_id(project, rel_path):
    """Deterministic identifier for a file within a project."""
    return stable_id("file", project, rel_path)


def symbol_id(project, rel_path, kind, qname):
    """Deterministic identifier for a symbol within a project."""
    return stable_id("sym", project, rel_path, kind, qname)


def edge_id(project, source, rel_type, target):
    """Deterministic identifier for a typed relationship."""
    return stable_id("edge", project, source, rel_type, target)


def module_qname_for(rel_path):
    """Dotted python module name for a rel_path, e.g. 'pkg/core.py' -> 'pkg.core'
    and 'pkg/__init__.py' -> 'pkg'. Used consistently by indexer and queries."""
    base = rel_path
    if base.endswith(".pyi"):
        base = base[:-4]
    elif base.endswith(".py"):
        base = base[:-3]
    else:
        base = base.rsplit(".", 1)[0]
    parts = [p for p in base.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


# -- models -----------------------------------------------------------------

# Deterministic per-file language classification. Configuration files are
# indexed as ``config`` data and are NEVER executed.
LANG_PYTHON = "python"
LANG_JAVASCRIPT = "javascript"
LANG_TYPESCRIPT = "typescript"
LANG_CONFIG = "config"
LANG_OTHER = "other"

# Parse outcomes for files that we attempt to index structurally.
STATUS_OK = "ok"
STATUS_SYNTAX_ERROR = "syntax_error"
STATUS_DECODE_ERROR = "decode_error"
STATUS_UNSUPPORTED = "unsupported"   # language recognised but not structured
STATUS_OVERSIZED = "oversized"
STATUS_UNREADABLE = "unreadable"
STATUS_PARSE_ABORTED = "parse_aborted"   # DoS guard tripped (depth/size)

SYMBOL_MODULE = "module"
SYMBOL_CLASS = "class"
SYMBOL_FUNCTION = "function"
SYMBOL_METHOD = "method"

# Relationship certainty: only edges the parser can establish directly are
# ``fact``; everything resolvable only by heuristic is ``candidate`` and must
# never be presented as a fact.
CERTAINTY_FACT = "fact"
CERTAINTY_CANDIDATE = "candidate"

# Relationship types.
REL_CONTAINS = "contains"        # module -> symbol           (fact)
REL_DEFINES = "defines"          # module -> class/function   (fact)
REL_DEFINES_CLASS = "defines_class"  # class -> method         (fact)
REL_IMPORTS = "imports"          # module -> module           (fact when resolved)
REL_INHERITS = "inherits"        # class -> base              (fact when resolved)
REL_CALLS = "calls"              # module -> module, candidate only
REL_TESTS = "tests"              # test file -> source file   (conservative)
REL_DEPENDS_ON = "depends_on"    # project -> external module (candidate)


@dataclass
class SourceLocation:
    """Exact 1-based line range in a file."""
    line_start: int
    line_end: int

    def as_dict(self):
        return {"line_start": self.line_start, "line_end": self.line_end}


@dataclass
class FileRecord:
    """One indexed file. Identity = (project_id, rel_path)."""
    project: str
    rel_path: str
    language: str
    size_bytes: int
    sha256: str
    mtime_ns: int
    parse_status: str
    parse_error: str = ""
    is_test: bool = False
    config_type: str = ""
    detail: str = ""   # JSON config file data (never executed)

    @property
    def id(self):
        return file_id(self.project, self.rel_path)

    def as_dict(self):
        d = {
            "id": self.id,
            "project": self.project,
            "rel_path": self.rel_path,
            "language": self.language,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "mtime_ns": self.mtime_ns,
            "parse_status": self.parse_status,
            "parse_error": self.parse_error,
            "is_test": bool(self.is_test),
            "config_type": self.config_type,
        }
        if self.detail:
            d["detail"] = self.detail
        return d


@dataclass
class SymbolRecord:
    """A structural symbol (module/class/function/method)."""
    project: str
    rel_path: str
    kind: str
    name: str
    qname: str
    line_start: int
    line_end: int
    parent_id: str = ""
    signature: str = ""
    decorators: str = ""     # comma-joined decorator names
    is_async: bool = False
    detail: str = ""         # JSON e.g. {"bases": [...], "methods": [...]}

    @property
    def id(self):
        return symbol_id(self.project, self.rel_path, self.kind, self.qname)

    def as_dict(self):
        d = {
            "id": self.id,
            "project": self.project,
            "rel_path": self.rel_path,
            "kind": self.kind,
            "name": self.name,
            "qname": self.qname,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }
        if self.parent_id:
            d["parent_id"] = self.parent_id
        if self.signature:
            d["signature"] = self.signature
        if self.decorators:
            d["decorators"] = self.decorators.split(",")
        if self.is_async:
            d["is_async"] = True
        if self.detail:
            d["detail"] = self.detail
        return d


@dataclass
class EdgeRecord:
    """A deterministic relationship with an explicit certainty."""
    project: str
    source_id: str
    rel_type: str
    target_id: str
    certainty: str
    target_name: str = ""      # unresolved name target (when no target_id)
    label: str = ""
    confidence: str = ""       # e.g. "high" for resolved, "candidate"

    @property
    def id(self):
        return edge_id(self.project, self.source_id, self.rel_type,
                       self.target_id or self.target_name)

    def as_dict(self):
        d = {
            "id": self.id,
            "project": self.project,
            "source_id": self.source_id,
            "rel_type": self.rel_type,
            "certainty": self.certainty,
        }
        if self.target_id:
            d["target_id"] = self.target_id
        if self.target_name:
            d["target_name"] = self.target_name
        if self.label:
            d["label"] = self.label
        if self.confidence:
            d["confidence"] = self.confidence
        return d
