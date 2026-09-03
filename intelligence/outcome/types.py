"""Outcome classification types (Phase 2).

Outcomes are *verified* classifications of execution results. The
classification is a deterministic function of the verification result.
"""

from enum import Enum


class OutcomeClassification(Enum):
    """Verified classification of an execution outcome.

    SUCCESS   -- the goal was met and verified.
    FAILURE   -- the goal was not met and the failure is verified.
    PARTIAL   -- part of the goal was met.
    BLOCKED   -- could not proceed (denied / skipped / interrupted).
    UNKNOWN   -- no determinable classification from the verification result.
    """

    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    BLOCKED = "blocked"
    UNKNOWN = "unknown"


# Status values accepted verbatim by synonyms, mapped to a classification.
_STATUS_CLASSIFICATION = {
    "success": OutcomeClassification.SUCCESS,
    "verified": OutcomeClassification.SUCCESS,
    "passed": OutcomeClassification.SUCCESS,
    "ok": OutcomeClassification.SUCCESS,
    "failed": OutcomeClassification.FAILURE,
    "error": OutcomeClassification.FAILURE,
    "unverified": OutcomeClassification.FAILURE,
    "partial": OutcomeClassification.PARTIAL,
    "blocked": OutcomeClassification.BLOCKED,
    "denied": OutcomeClassification.BLOCKED,
    "skipped": OutcomeClassification.BLOCKED,
}


def classify_outcome(verification_result) -> OutcomeClassification:
    """Deterministic classification from a verification result.

    ``verification_result`` may be a dict (with ``status`` or ``ok`` keys) or
    a string status. Unknown inputs map to :attr:`OutcomeClassification.
    UNKNOWN`.
    """
    if isinstance(verification_result, str):
        return _STATUS_CLASSIFICATION.get(
            verification_result.lower(), OutcomeClassification.UNKNOWN)
    if isinstance(verification_result, dict):
        status = verification_result.get("status")
        if status is not None and not isinstance(status, bool):
            mapped = _STATUS_CLASSIFICATION.get(
                str(status).lower())
            if mapped is not None:
                return mapped
        ok = verification_result.get("ok")
        if ok is True:
            return OutcomeClassification.SUCCESS
        if ok is False:
            return OutcomeClassification.FAILURE
        passed = verification_result.get("passed")
        if passed is True:
            return OutcomeClassification.SUCCESS
        if passed is False:
            return OutcomeClassification.FAILURE
    return OutcomeClassification.UNKNOWN
