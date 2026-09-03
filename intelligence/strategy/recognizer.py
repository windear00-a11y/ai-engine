"""Recognize strategy candidates from experience patterns (Phase 4).

Recognition is deterministic: identical experience records always produce the
same candidates. A candidate is emitted for each problem class (task type)
that has been observed at least ``min_samples`` times. Confidence is derived
from sample count and success rate (see :func:`candidate_confidence`).
"""

from collections import defaultdict

from .types import StrategyCandidate, candidate_confidence

# A task type maps to a strategy problem_class. Recognition groups by task
# type because experience records carry their task type.
_PROBLEM_CLASS_FIELD = "task_type"


def _outcome_ok(summary):
    if not isinstance(summary, dict):
        return False
    outcome = str(summary.get("outcome", "")).lower()
    return outcome in ("success", "succeeded", "ok", "passed", "pass")


def recognize_strategies_from_experience(experience_records, min_samples=3):
    """Return a list of :class:`StrategyCandidate` for repeated patterns.

    Each experience record must expose a ``task_type`` attribute (matching
    Phase 3's ExperienceRecord) and a ``summary`` dict whose ``outcome``
    indicates success. Records lacking a task type are ignored.
    """
    groups = defaultdict(list)
    for record in experience_records:
        task_type = getattr(record, "task_type", None)
        if task_type:
            groups[task_type].append(record)

    candidates = []
    for problem_class, records in sorted(groups.items()):
        records = sorted(records, key=lambda r: getattr(r, "experience_id",
                                                        ""))
        sample_count = len(records)
        if sample_count < min_samples:
            continue
        successes = sum(1 for r in records if _outcome_ok(
            getattr(r, "summary", None)))
        success_rate = successes / sample_count
        evidence_ids = []
        for r in records:
            for eid in getattr(r, "evidence_ids", ()) or ():
                if eid not in evidence_ids:
                    evidence_ids.append(eid)
        candidates.append(StrategyCandidate(
            problem_class=problem_class,
            sample_count=sample_count,
            success_rate=success_rate,
            confidence=candidate_confidence(sample_count, success_rate,
                                            min_samples),
            evidence_ids=evidence_ids,
            recommended_tool_sequence=(),
        ))
    # Deterministic ordering
    candidates.sort(key=lambda c: (
        -c.confidence, -c.sample_count, c.problem_class))
    return candidates
