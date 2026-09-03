"""Strategy type definitions (Phase 4).

Strategies encode reusable approaches to problem classes. A strategy is a
first-class, stored object that the Decision Engine selects against. They are
either explicitly defined or recognized from repeated experience patterns.
"""

import enum


class StrategyType(enum.Enum):
    """Provenance of a strategy.

    ``EXPERIENCE_DERIVED`` -- recognized from repeated experience patterns.
    ``EXPLICIT``          -- defined by the operator (or seeded from the
                             deterministic planner).
    """

    EXPERIENCE_DERIVED = "experience_derived"
    EXPLICIT = "explicit"


class StrategyCandidate:
    """A candidate strategy proposed from a repeated experience pattern.

    Recognition is deterministic: identical experience records always yield
    identical candidates (same sample count, success rate, confidence).
    """

    __slots__ = (
        "problem_class",
        "sample_count",
        "success_rate",
        "confidence",
        "evidence_ids",
        "recommended_tool_sequence",
    )

    def __init__(self, problem_class, sample_count, success_rate, confidence,
                 evidence_ids, recommended_tool_sequence):
        self.problem_class = problem_class
        self.sample_count = sample_count
        self.success_rate = success_rate
        self.confidence = confidence
        self.evidence_ids = tuple(evidence_ids)
        self.recommended_tool_sequence = tuple(recommended_tool_sequence)


def candidate_confidence(sample_count, success_rate, min_samples):
    """Deterministic confidence from sample count and success rate.

    Confidence grows with samples toward the observed success rate, and is
    damped by a conservative prior on few samples::

        prior = 0.5
        n     = sample_count
        conf  = (n * success_rate + prior) / (n + 1)

    With ``min_samples`` observations required for any candidate at all, this
    yields a monotonically improving, reproducible value.
    """
    n = max(0, sample_count)
    prior = 0.5
    if n <= 0:
        return 0.0
    return round((n * success_rate + prior) / (n + 1), 6)
