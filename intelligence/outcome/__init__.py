"""Outcome classification and recording (Phase 2).

Outcomes are verified classifications of execution results. Recording an
outcome requires verification evidence (enforced by the recorder).
"""

from intelligence.outcome.extractor import extract_outcomes_from_journal
from intelligence.outcome.recorder import (
    VerificationRequiredError,
    get_outcome,
    outcomes_for_plan,
    record_outcome,
)
from intelligence.outcome.schema import Outcome, derive_outcome_id
from intelligence.outcome.store import OutcomeStore, default_evidence_db_path
from intelligence.outcome.types import OutcomeClassification, classify_outcome

__all__ = [
    "record_outcome",
    "get_outcome",
    "outcomes_for_plan",
    "classify_outcome",
    "extract_outcomes_from_journal",
    "VerificationRequiredError",
    "Outcome",
    "OutcomeClassification",
    "OutcomeStore",
    "derive_outcome_id",
    "default_evidence_db_path",
]
