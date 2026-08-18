"""Deterministic candidate validation (VALIDATED state).

Checks are purely mechanical -- they verify that a candidate's provenance is
grounded in the actual source text:

* the candidate has the required provenance fields (document, section, location,
  evidence);
* the source is non-empty;
* the evidence that a candidate claims to come from a line range really is
  present in the raw document text at that range.

A candidate that passes becomes VALIDATED; one that fails stays a candidate with
an attached record. Either way, validation NEVER turns a candidate into trusted
knowledge -- import is a separate, deliberate, manual step.
"""

from knowledge_compiler.types import (
    ValidationRecord, CANDIDATE, VALIDATED,
)


def validate_candidates(doc, candidates):
    """Validate every candidate of one document; return ValidationRecord list."""
    records = []
    lines = (doc.text or "").split("\n")
    n = len(lines)
    for cand in candidates:
        reasons = []
        if not cand.document:
            reasons.append("missing document provenance")
        if not isinstance(cand.section_path, list):
            reasons.append("missing section provenance")
        if not cand.evidence:
            reasons.append("missing evidence text")
        if not cand.location:
            reasons.append("missing source location")
        # Evidence grounded check.
        if cand.location and lines:
            start = cand.location.line_start
            end = cand.location.line_end
            if start < 1 or end > n or start > end:
                reasons.append(
                    f"line range {start}..{end} out of bounds (document has {n} lines)")
            else:
                window = "\n".join(lines[start - 1:end])
                if not _evidence_fits(cand.evidence, window):
                    reasons.append(
                        "evidence text does not match the source lines at that location")
        if reasons:
            cand.state = CANDIDATE
            cand.meta["validation_error"] = reasons
            records.append(ValidationRecord(
                candidate_id=cand.candidate_id, valid=False, reasons=reasons))
        else:
            cand.state = VALIDATED
            records.append(ValidationRecord(
                candidate_id=cand.candidate_id, valid=True, reasons=[]))
    return records


def _evidence_fits(evidence, window):
    ev = "\n".join(l.rstrip() for l in evidence.splitlines())
    win = "\n".join(l.rstrip() for l in window.splitlines())
    return ev == win or ev in win