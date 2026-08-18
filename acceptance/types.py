"""Knowledge Acceptance Layer -- decision records and shared types.

The acceptance layer sits between Knowledge Compiler candidates
(``CANDIDATE`` / ``VALIDATED``) and the existing ingestion/SQLite system:

    CANDIDATE -> VALIDATED -> ACCEPTED / HELD / REJECTED -> IMPORT PREVIEW

It is a deterministic *trust boundary*: every candidate receives exactly one
decision (ACCEPT / HOLD / REJECT) based on explicit, machine-verifiable rules.
A decision does NOT assert that the source is universally true -- it only means
"this candidate satisfies our explicit deterministic acceptance rules". The raw
evidence and provenance remain authoritative for auditing, and REJECT does NOT
delete anything: candidate records stay inspectable.

Everything in this module is deterministic: identities, fingerprints and
canonical ordering are pure functions of the candidate fields.
"""

import re
from dataclasses import dataclass, field
from typing import Optional

from knowledge_compiler.types import (
    stable_id,
    CAND_API, CAND_INHERITANCE, CAND_DEFINITION, CAND_DEPENDENCY,
    CAND_PROCEDURE, CAND_CODE_EXAMPLE, CAND_REFERENCE,
)

# -- decisions (every candidate gets exactly one) ----------------------------
ACCEPT = "ACCEPT"
HOLD = "HOLD"
REJECT = "REJECT"
DECISIONS = (ACCEPT, HOLD, REJECT)

# Conceptual pipeline states introduced by this layer.
ACCEPTED = "ACCEPTED"
HELD = "HELD"
REJECTED = "REJECTED"

# Candidate kinds this layer knows how to evaluate (mirrors the extractor's
# full set -- unknown kinds are rejected as unsupported/malformed).
KNOWN_KINDS = (
    CAND_API, CAND_INHERITANCE, CAND_DEFINITION, CAND_DEPENDENCY,
    CAND_PROCEDURE, CAND_CODE_EXAMPLE, CAND_REFERENCE,
)

# Inline roles that are documentation navigation / UI-environment surfaces
# rather than knowledge-bearing references. (The extractor already keeps these
# out of candidates; this set makes the policy defensive if they ever appear.)
NAVIGATION_ROLES = {"ref", "doc", "numref"}
UI_ENVIRONMENT_ROLES = {"file", "kbd", "option", "mailheader", "guilabel",
                        "menuselection", "command", "envvar"}

# Code-block sources that are test/fixture/generated configuration.
NON_EXAMPLE_SOURCES = {"testsetup", "testcode", "testoutput",
                       "productionlist"}
# Language markers for shell / text transcripts. ``pycon`` (interactive Python
# with ``>>>`` prompts) is intentionally NOT here: those blocks ARE genuine
# user-facing examples in the canonical docs. ``sh``/``bash``/``doscon``/
# ``ps1con``/``powershell``/``zsh``/``fish``/``cmd`` are command-line
# invocations, not Python knowledge examples.
SHELL_LANGUAGES = {"shell", "shell-session", "console", "text", "none",
                   "sh", "bash", "zsh", "fish", "cmd", "doscon", "ps1con",
                   "powershell"}
# Deterministic non-Python REPL/debugger prompt markers. A code example whose
# first code line is one of these is a transcript, not a Python example.
TRANSCRIPT_PROMPTS = ("(gdb)", "(Pdb)", "(pdb)")

# -- strong deterministic identity fields -------------------------------------
# The identity of a candidate is built from kind-specific, structurally strong
# fields -- NEVER from the human-written summary alone. Two candidates share an
# identity only when they refer to the *same* documented thing.
IDENTITY_FIELDS = {
    CAND_API: ("directive", "name"),              # namespace + declared symbol
    CAND_INHERITANCE: ("class", "base"),          # meta["class"/"base"]
    CAND_DEFINITION: ("term",),                   # meta["term"]
    CAND_DEPENDENCY: ("module",),                 # meta["module"]
    CAND_PROCEDURE: ("heading",),                 # meta["heading"]
    CAND_CODE_EXAMPLE: ("code_fingerprint",),     # normalized evidence
    CAND_REFERENCE: ("role", "target"),           # role+target / url
}

_FRAGMENT_START = (".", ",", ")", "]", "}", ":", "...")


def _text(value):
    return value if isinstance(value, str) else ""


def normalize_code(text):
    """Collapse whitespace/blank lines for code identity (line-insensitive)."""
    lines = []
    for ln in _text(text).splitlines():
        s = ln.strip()
        if s:
            lines.append(s)
    return "\n".join(lines)


def content_signature(kind, cand):
    """Deterministic content key used to detect identity conflicts.

    Two candidates that share an identity (same strong identity fields) are
    only merged when their *content* is equivalent; different content under the
    same identity means the identity cannot be safely resolved.
    """
    meta = cand.get("meta") or {}
    if kind == CAND_API:
        return _text(meta.get("signature")) or _text(cand.get("summary"))
    if kind == CAND_INHERITANCE:
        return "%s -> %s" % (meta.get("class", ""), meta.get("base", ""))
    if kind == CAND_DEFINITION:
        return "%s\n%s" % (meta.get("term", ""), meta.get("definition", ""))
    if kind == CAND_DEPENDENCY:
        return _text(meta.get("module"))
    if kind == CAND_PROCEDURE:
        return _text(meta.get("heading")) or _text(cand.get("summary"))
    if kind == CAND_CODE_EXAMPLE:
        return normalize_code(cand.get("evidence"))
    if kind == CAND_REFERENCE:
        if meta.get("kind") == "link":
            return "%s|%s" % (meta.get("url", ""), meta.get("text", ""))
        return "%s|%s" % (meta.get("role", ""), meta.get("target", ""))
    return _text(cand.get("summary"))


def identity_fields(kind, cand):
    """Return the strong identity field values for a candidate (as a dict)."""
    meta = cand.get("meta") or {}
    if kind == CAND_CODE_EXAMPLE:
        return {"code_fingerprint": normalize_code(cand.get("evidence"))}
    if kind == CAND_REFERENCE and meta.get("kind") == "link":
        return {"role": "link", "target": meta.get("url", "")}
    values = {}
    for key in IDENTITY_FIELDS.get(kind, ()):
        values[key] = meta.get(key, "")
    return values


def compute_identity(kind, cand):
    """Deterministic identity fingerprint for a candidate.

    The fingerprint is a stable hash over ``kind`` plus the strong identity
    fields -- it never uses the summary as identity, so two candidates with
    similar (or even identical) summaries but different meaning keep different
    identities.
    """
    values = identity_fields(kind, cand)
    parts = [kind]
    for key in IDENTITY_FIELDS.get(kind, ()):
        parts.append(_text(values.get(key, "")))
    return stable_id(*parts)


def is_ambiguous_fragment(summary):
    """A code example that begins with a continuation/placeholder fragment.

    Deterministic: a leading operator, closing bracket or ``...`` means the
    snippet cannot stand alone as an example (it continues the previous block).
    """
    s = _text(summary).strip()
    if not s:
        return True
    return s.startswith(_FRAGMENT_START)


def reason_code(decision, kind, base):
    """Compose a stable, scoped reason code (no free-form strings)."""
    return "%s:%s:%s" % (decision, kind, base)


@dataclass
class DecisionRecord:
    """Exactly one deterministic decision for one candidate."""
    candidate_id: str
    kind: str
    decision: str            # ACCEPT | HOLD | REJECT
    reason_code: str
    reason: str
    confidence: str
    identity: str
    summary: str
    document: str
    section_path: list = field(default_factory=list)
    location: dict = field(default_factory=dict)
    evidence: str = ""

    @property
    def provenance(self):
        return {
            "document": self.document,
            "section_path": list(self.section_path),
            "location": dict(self.location),
        }

    def as_dict(self):
        return {
            "candidate_id": self.candidate_id,
            "candidate_kind": self.kind,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "confidence": self.confidence,
            "identity": self.identity,
            "summary": self.summary,
            "provenance": self.provenance,
            "evidence": self.evidence,
        }


@dataclass
class IdentityGroup:
    """One canonical knowledge identity with all its supporting candidates."""
    identity: str
    kind: str
    canonical_candidate_id: str
    members: list = field(default_factory=list)   # candidate dicts
    conflict: bool = False
    resolved_endpoints: Optional[dict] = None  # inheritance: accepted node ids

    @property
    def member_count(self):
        return len(self.members)

    @property
    def documents(self):
        seen = []
        for m in self.members:
            doc = m.get("document")
            if doc not in seen:
                seen.append(doc)
        return sorted(seen)

    @property
    def provenances(self):
        return [m for m in self.members]

    def as_dict(self):
        return {
            "identity": self.identity,
            "kind": self.kind,
            "canonical_candidate_id": self.canonical_candidate_id,
            "member_count": len(self.members),
            "documents": self.documents,
            "conflict": self.conflict,
        }
