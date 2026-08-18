"""Knowledge Compiler -- intermediate representations and shared types.

These types model the pipeline's conceptual states:

    RAW  ->  EXTRACTED  ->  CANDIDATE  ->  VALIDATED  ->  IMPORTED

* ``RAW``       the untouched source files on disk.
* ``EXTRACTED`` a parsed :class:`Document` of structure chunks (:class:`Chunk`).
* ``CANDIDATE`` a deterministic :class:`Candidate` derived from explicit
                evidence in the extracted structure.
* ``VALIDATED`` a candidate whose provenance/evidence passed deterministic
                checks (:class:`ValidationRecord`).
* ``IMPORTED``  a candidate that a human explicitly chose to import into the
                knowledge database (v1: never done automatically).

This module is deliberately adapter-agnostic: the RST adapter and any future
adapter produce these types, and nothing this module imports ties it to Python.
"""

import hashlib
from dataclasses import dataclass, field

# -- conceptual states -------------------------------------------------------
RAW = "RAW"
EXTRACTED = "EXTRACTED"
CANDIDATE = "CANDIDATE"
VALIDATED = "VALIDATED"
IMPORTED = "IMPORTED"

STATES = (RAW, EXTRACTED, CANDIDATE, VALIDATED, IMPORTED)

# -- extracted chunk kinds (document structure) ------------------------------
CHUNK_TITLE = "title"
CHUNK_SECTION = "section"
CHUNK_PARAGRAPH = "paragraph"
CHUNK_CODE = "code_block"
CHUNK_DIRECTIVE = "directive"
CHUNK_REFERENCE = "reference"
CHUNK_SIGNATURE = "api_signature"
CHUNK_LIST = "list"
CHUNK_ADMONITION = "admonition"
CHUNK_COMMENT = "comment"

# -- candidate kinds ---------------------------------------------------------
CAND_API = "api_declaration"
CAND_INHERITANCE = "inheritance"
CAND_DEFINITION = "definition"
CAND_DEPENDENCY = "dependency"
CAND_PROCEDURE = "procedure"
CAND_CODE_EXAMPLE = "code_example"
CAND_REFERENCE = "reference"

# Only candidates with these kinds are ever produced by the extractor; every
# one is backed by explicit, machine-verifiable document structure.
CANDIDATE_KINDS = (
    CAND_API, CAND_INHERITANCE, CAND_DEFINITION, CAND_DEPENDENCY,
    CAND_PROCEDURE, CAND_CODE_EXAMPLE, CAND_REFERENCE,
)


def stable_id(*parts):
    """Deterministic id from arbitrary string parts (stable across runs)."""
    canonical = "|".join(str(p) for p in parts).encode("utf-8", "replace")
    return hashlib.sha256(canonical).hexdigest()[:20]


@dataclass
class SourceLocation:
    """Exact 1-based line range in the document plus its relative path."""
    path: str = ""
    line_start: int = 1
    line_end: int = 1

    def as_dict(self):
        return {
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


@dataclass
class Chunk:
    """A single extracted structural unit with exact source evidence."""
    kind: str
    content: str                 # normalized/structured representation
    evidence: str                # exact verbatim source text for this unit
    location: SourceLocation = None
    section_path: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    state: str = EXTRACTED

    def as_dict(self):
        return {
            "kind": self.kind,
            "content": self.content,
            "evidence": self.evidence,
            "location": self.location.as_dict(),
            "section_path": list(self.section_path),
            "meta": dict(self.meta),
            "state": self.state,
        }


@dataclass
class Document:
    """One parsed source document (EXTRACTED state). Raw text is kept only in
    memory for provenance checks and is never serialized."""
    path: str = ""               # absolute filesystem path
    rel_path: str = ""           # path relative to the scanned source root
    source_name: str = ""        # corpus / scanned-source identifier (e.g. tutorial)
    adapter: str = ""            # adapter name, e.g. "rst"
    title: str = None
    chunks: list = field(default_factory=list)
    errors: list = field(default_factory=list)   # [(line, message)]
    state: str = EXTRACTED
    text: str = ""               # raw text, in-memory only (not serialized)

    def as_dict(self):
        return {
            "path": self.path,
            "rel_path": self.rel_path,
            "source_name": self.source_name,
            "adapter": self.adapter,
            "title": self.title,
            "chunks": [c.as_dict() for c in self.chunks],
            "errors": [{"line": ln, "message": msg} for ln, msg in self.errors],
            "state": self.state,
        }


@dataclass
class Candidate:
    """A deterministic, evidence-backed knowledge candidate (CANDIDATE state).

    A candidate is NEVER trusted knowledge. It must pass validation and an
    explicit human decision before it can be imported into SQLite.
    """
    kind: str
    summary: str
    evidence: str
    document: str = ""           # rel_path
    section_path: list = field(default_factory=list)
    location: SourceLocation = None
    confidence: str = "high"
    meta: dict = field(default_factory=dict)
    state: str = CANDIDATE
    candidate_id: str = None

    def as_dict(self):
        cid = self.candidate_id or stable_id(
            self.document, self.kind, self.location.line_start,
            self.location.line_end, self.evidence, self.summary)
        return {
            "candidate_id": cid,
            "kind": self.kind,
            "summary": self.summary,
            "evidence": self.evidence,
            "document": self.document,
            "section_path": list(self.section_path),
            "location": self.location.as_dict() if self.location else None,
            "confidence": self.confidence,
            "meta": dict(self.meta),
            "state": self.state,
        }


@dataclass
class ValidationRecord:
    """Result of the deterministic validation pass for one candidate."""
    candidate_id: str
    valid: bool
    reasons: list = field(default_factory=list)
    state: str = VALIDATED

    def as_dict(self):
        return {
            "candidate_id": self.candidate_id,
            "valid": self.valid,
            "reasons": list(self.reasons),
            "state": self.state,
        }