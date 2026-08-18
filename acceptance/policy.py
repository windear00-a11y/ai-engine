"""Deterministic acceptance policies (the trust-boundary rules).

Every candidate is evaluated by exactly one rule that yields a decision:

ACCEPT -- high-confidence, explicitly-evidenced, machine-verifiable knowledge:
    * explicit API declarations
    * explicit inheritance declarations
    * explicit glossary definitions
    * strong procedures with procedural body evidence
    * high-confidence standard-library dependencies
    * genuine user-facing code examples with proper provenance

HOLD   -- structurally valid but not strong enough to trust on its own:
    * low-confidence / local / unknown dependencies
    * ambiguous (fragment) code examples
    * candidates requiring cross-document resolution (references)
    * identity conflicts that cannot yet be safely resolved
    * candidates with no safe Knowledge Schema mapping

REJECT -- never trusted knowledge (but NEVER deleted -- the raw evidence and
    candidate records remain inspectable):
    * documentation navigation references
    * UI/environment-only references
    * generated/test configuration blocks
    * shell/text transcripts
    * malformed / unsupported candidates
    * candidates whose validation failed (ungrounded evidence)
    * obvious extraction artifacts

These rules are pure functions of the candidate record: same input, same
decision, every run.
"""

import re

from acceptance.types import (
    ACCEPT, HOLD, REJECT,
    NAVIGATION_ROLES, UI_ENVIRONMENT_ROLES, NON_EXAMPLE_SOURCES,
    SHELL_LANGUAGES, TRANSCRIPT_PROMPTS, KNOWN_KINDS, is_ambiguous_fragment,
    reason_code,
)
from knowledge_compiler.types import CAND_REFERENCE, CAND_CODE_EXAMPLE

# -- reason codes (stable, scoped) -------------------------------------------

ACCEPT_API = reason_code(ACCEPT, "api_declaration", "explicit_api_declaration")
ACCEPT_INHERITANCE = reason_code(ACCEPT, "inheritance",
                                 "explicit_inheritance_declaration")
ACCEPT_DEFINITION = reason_code(ACCEPT, "definition",
                                "explicit_glossary_definition")
ACCEPT_PROCEDURE = reason_code(ACCEPT, "procedure", "procedure_with_body_evidence")
ACCEPT_DEPENDENCY = reason_code(ACCEPT, "dependency", "stdlib_dependency")
ACCEPT_EXAMPLE = reason_code(ACCEPT, "code_example", "genuine_code_example")

HOLD_DEPENDENCY = reason_code(HOLD, "dependency", "local_or_unknown_dependency")
HOLD_AMBIGUOUS = reason_code(HOLD, "code_example", "ambiguous_code_fragment")
HOLD_SETUP = reason_code(HOLD, "code_example", "setup_configuration_only")
HOLD_DEFINITION = reason_code(HOLD, "definition", "missing_definition_text")
HOLD_REFERENCE = reason_code(HOLD, "reference",
                             "cross_document_reference_resolution")
HOLD_CONFLICT = reason_code(HOLD, "identity", "duplicate_identity_conflict")
HOLD_UNMAPPED = reason_code(HOLD, "schema", "unmappable_to_schema")
HOLD_CONFIDENCE = reason_code(HOLD, "confidence", "insufficient_confidence")

# Inheritance endpoint rules: an ACCEPTED ``extends`` relationship must be
# backed by ACCEPTED source/target nodes. Nothing is invented to satisfy an
# edge; if an endpoint cannot be represented by an existing accepted node the
# relationship is HELD with a precise reason.
HOLD_INHERITANCE_SOURCE = reason_code(
    HOLD, "inheritance", "inheritance_source_not_accepted")
HOLD_INHERITANCE_TARGET_NOT_ACCEPTED = reason_code(
    HOLD, "inheritance", "inheritance_target_not_accepted")
HOLD_INHERITANCE_TARGET_UNRESOLVED = reason_code(
    HOLD, "inheritance", "inheritance_target_unresolved")
HOLD_INHERITANCE_TARGET_AMBIGUOUS = reason_code(
    HOLD, "inheritance", "inheritance_target_ambiguous")

REJECT_NAV = reason_code(REJECT, "reference", "navigation_reference")
REJECT_UI = reason_code(REJECT, "reference", "ui_environment_reference")
REJECT_TEST = reason_code(REJECT, "code_example", "generated_or_test_block")
REJECT_SHELL = reason_code(REJECT, "code_example", "shell_or_text_transcript")
REJECT_MALFORMED = reason_code(REJECT, "candidate", "malformed_candidate")
REJECT_VALIDATION = reason_code(REJECT, "validation", "validation_failed")
REJECT_ARTIFACT = reason_code(REJECT, "candidate", "extraction_artifact")

_REASONS = {
    ACCEPT_API: "explicit API declaration",
    ACCEPT_INHERITANCE: "explicit inheritance declaration",
    ACCEPT_DEFINITION: "explicit glossary definition",
    ACCEPT_PROCEDURE: "procedure with explicit how-to heading and body evidence",
    ACCEPT_DEPENDENCY: "high-confidence standard-library dependency",
    ACCEPT_EXAMPLE: "genuine user-facing code example with source provenance",
    HOLD_DEPENDENCY: "local/unknown or builtin-only dependency -- not safe to trust as external",
    HOLD_AMBIGUOUS: "code example begins with a continuation/placeholder fragment",
    HOLD_SETUP: "code example is pure import/setup configuration -- no example value beyond the dependency",
    HOLD_DEFINITION: "glossary term without definition text -- insufficient evidence for a concept node",
    HOLD_REFERENCE: "reference requires cross-document resolution before trust",
    HOLD_CONFLICT: "duplicate identity cannot be safely resolved (conflicting content)",
    HOLD_UNMAPPED: "no safe deterministic mapping to an existing node type",
    HOLD_CONFIDENCE: "structurally valid but confidence is not high",
    HOLD_INHERITANCE_SOURCE: "inheritance source class is not an accepted node -- no evidence-backed node exists",
    HOLD_INHERITANCE_TARGET_NOT_ACCEPTED: "inheritance target is declared in the corpus but is not an accepted node",
    HOLD_INHERITANCE_TARGET_UNRESOLVED: "inheritance target has no declaration evidence -- cannot resolve to a node",
    HOLD_INHERITANCE_TARGET_AMBIGUOUS: "inheritance target is declared under multiple different directives (ambiguous node type)",
    REJECT_NAV: "documentation navigation reference -- not trusted knowledge",
    REJECT_UI: "UI/environment-only reference -- not trusted knowledge",
    REJECT_TEST: "generated/test configuration block -- not a knowledge example",
    REJECT_SHELL: "shell/debugger/text transcript -- not a code example",
    REJECT_MALFORMED: "malformed or unsupported candidate record",
    REJECT_VALIDATION: "candidate failed pre-trust validation (ungrounded evidence)",
    REJECT_ARTIFACT: "obvious extraction artifact",
}


def reason_text(code):
    """Human-readable text for a reason code (never empty)."""
    return _REASONS.get(code, code) or code


_IMPORT_LINE = re.compile(
    r"^(?:from\s+[\w.]+\s+import\s+(?:[\w.*]+(?:\s+as\s+\w+)?)|"
    r"import\s+[\w.]+(?:\s+as\s+\w+)?)$")


def _is_shell_transcript(cand):
    """Whole-block shell/text signals -- not snippets that merely print them.

    Rejects blocks whose language marker is a shell/text language, whose FIRST
    code line is a shell command / shebang, or whose first code line is a
    non-Python REPL/debugger prompt (e.g. ``(gdb)``). A ``#!`` or ``$`` line
    that appears as doctest *output* farther down is still a genuine example.
    """
    meta = cand.get("meta") or {}
    if (meta.get("language") or "").lower() in SHELL_LANGUAGES:
        return True
    first = None
    for ln in (cand.get("evidence") or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        first = s
        break
    if first is None:
        return False
    if first.startswith("$ ") or first.startswith("#!"):
        return True
    if first.startswith(TRANSCRIPT_PROMPTS):
        return True
    return False


def _is_imports_only(evidence):
    """Pure import/setup block (optionally REPL-prompted).

    ``import x`` / ``from x import y`` lines only -- a line containing a
    semicolon (``import pdb; pdb.set_trace()``) or any non-import code means
    the block demonstrates something, so it is NOT classified as setup-only.
    """
    lines = []
    for ln in (evidence or "").splitlines():
        s = ln.strip()
        if s.startswith(">>>") or s.startswith("..."):
            s = s[3:].strip()
        if not s:
            continue
        lines.append(s)
    if not lines:
        return False
    if any(";" in s for s in lines):
        return False
    return all(_IMPORT_LINE.match(s) for s in lines)


def malformed_reason(cand, valid):
    """Return a REJECT reason code if the record is malformed, else None."""
    if not valid:
        return REJECT_VALIDATION
    cid = cand.get("candidate_id")
    if not cid:
        return REJECT_MALFORMED
    if cand.get("kind") not in KNOWN_KINDS:
        return REJECT_MALFORMED
    if not cand.get("summary") or not cand.get("evidence"):
        return REJECT_ARTIFACT
    if not cand.get("document"):
        return REJECT_MALFORMED
    if not cand.get("location"):
        return REJECT_MALFORMED
    return None


def decide(cand, valid):
    """Return (decision, reason_code) for one candidate (pre-identity rules).

    ``valid`` is the candidate's pre-trust validation status from the
    compiler. Overrides for identity conflicts and schema mapping are applied
    by the evaluator AFTER this function.
    """
    bad = malformed_reason(cand, valid)
    if bad is not None:
        if bad == REJECT_VALIDATION:
            return REJECT, REJECT_VALIDATION
        if bad == REJECT_ARTIFACT:
            return REJECT, REJECT_ARTIFACT
        return REJECT, REJECT_MALFORMED

    kind = cand.get("kind")
    confidence = (cand.get("confidence") or "high").lower()
    meta = cand.get("meta") or {}

    if kind == "api_declaration":
        if confidence != "high":
            return HOLD, HOLD_CONFIDENCE
        return ACCEPT, ACCEPT_API

    if kind == "inheritance":
        return ACCEPT, ACCEPT_INHERITANCE

    if kind == "definition":
        if not (meta.get("definition") or "").strip():
            return HOLD, HOLD_DEFINITION
        return ACCEPT, ACCEPT_DEFINITION

    if kind == "dependency":
        origin = meta.get("origin")
        if meta.get("module") == "__future__":
            return HOLD, HOLD_DEPENDENCY
        if origin == "stdlib" and confidence == "high":
            return ACCEPT, ACCEPT_DEPENDENCY
        return HOLD, HOLD_DEPENDENCY

    if kind == "procedure":
        return ACCEPT, ACCEPT_PROCEDURE

    if kind == "code_example":
        if meta.get("source") in NON_EXAMPLE_SOURCES:
            return REJECT, REJECT_TEST
        if _is_shell_transcript(cand):
            return REJECT, REJECT_SHELL
        if is_ambiguous_fragment(cand.get("summary")):
            return HOLD, HOLD_AMBIGUOUS
        if _is_imports_only(cand.get("evidence")):
            return HOLD, HOLD_SETUP
        return ACCEPT, ACCEPT_EXAMPLE

    if kind == CAND_REFERENCE:
        role = meta.get("role") or ""
        if meta.get("kind") == "link":
            return REJECT, REJECT_NAV
        if role in UI_ENVIRONMENT_ROLES:
            return REJECT, REJECT_UI
        if role in NAVIGATION_ROLES:
            return REJECT, REJECT_NAV
        # Knowledge-bearing reference (func/class/mod/term/...): the actual
        # knowledge is defined elsewhere (cross-document) -- HOLD, never trust
        # a bare pointer as standalone knowledge.
        return HOLD, HOLD_REFERENCE

    return REJECT, REJECT_MALFORMED
