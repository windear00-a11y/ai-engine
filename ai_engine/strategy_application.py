"""Canonical, deterministic Strategy Application (Phase 28).

Pure domain module: stdlib only, no database, no network, no LLM. It turns a
bounded, ordered set of strategy candidates and a current situation into one
machine-readable applicability result. It never decides to follow a strategy;
it only determines fit. Applying a strategy here is NOT approval to execute.

Phase 25 integration: these semantics extend (they do not duplicate or
contradict) the Phase 25 ``should_apply_strategy`` rules:

* a superseded or deprecated strategy is never applicable;
* a strategy with context restrictions is applicable only inside an allowed
  context -- the restrictions must be satisfied, never assumed;
* a strategy applies only when the request's canonical situation pattern
  matches its problem class;
* a missing current context when restrictions exist is surfaced as
  INSUFFICIENT_EVIDENCE instead of being silently assumed to match;
* identical inputs always yield identical results (deterministic ids,
  ordering, conflict representation: no wall-clock time, no hash order, no
  database row order).
"""

import hashlib
import json

APP_APPLICABLE = "applicable"
APP_NOT_APPLICABLE = "not_applicable"
APP_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
APP_CONFLICTING = "conflicting"
APP_SUPERSEDED = "superseded"
APP_DEPRECATED = "deprecated"
APP_UNKNOWN = "unknown"
APP_NO_STRATEGY = "no_strategy"

DEFAULT_MAX_CANDIDATES = 20

_ALL_STATUSES = (
    APP_APPLICABLE,
    APP_NOT_APPLICABLE,
    APP_INSUFFICIENT_EVIDENCE,
    APP_CONFLICTING,
    APP_SUPERSEDED,
    APP_DEPRECATED,
    APP_UNKNOWN,
    APP_NO_STRATEGY,
)


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def canonical_pattern(value):
    """Canonical pattern for a situation string (case/whitespace collapsed).

    Mirrors the existing ``_canonical_pattern`` convention so application
    ordering compares the same normalized form used by Phase 25 learning.
    """
    if not isinstance(value, str):
        value = str(value)
    return " ".join(value.strip().lower().split())


def derive_application_id(situation, strategy_ids, context_id=None):
    """Deterministic application identity over the request inputs."""
    digest = hashlib.sha256(_canonical({
        "situation": canonical_pattern(situation),
        "strategy_ids": sorted(str(x) for x in strategy_ids or ()),
        "context_id": context_id,
    }).encode("utf-8")).hexdigest()
    return "sa_" + digest[:32]


def _pattern_relation(problem_class, situation):
    """Tri-state problem-class fit.

    Returns True (matches), False (does not match) or None (cannot be
    evaluated -- identity is deliberately preserved as uncertainty).
    """
    pattern = canonical_pattern(problem_class or "")
    value = canonical_pattern(situation)
    if not pattern or not value:
        return None
    if value == pattern or pattern in value or value in pattern:
        return True
    if set(value.split()) & set(pattern.split()):
        return None
    return False


def _allowed_contexts(strategy):
    restrictions = strategy.get("context_restrictions") or {}
    allowed = restrictions.get("allowed_contexts") or []
    if not allowed:
        allowed = (strategy.get("constraints") or {}).get(
            "applicable_contexts") or []
    return [str(x) for x in allowed]


def _context_state(strategy, context_id):
    """Context restriction check for one candidate.

    Returns None when there are no restrictions, otherwise one of
    APP_INSUFFICIENT_EVIDENCE / APP_NOT_APPLICABLE / APP_APPLICABLE.
    """
    allowed = _allowed_contexts(strategy)
    if not allowed:
        return None
    if context_id is None:
        return APP_INSUFFICIENT_EVIDENCE
    if str(context_id) in allowed:
        return APP_APPLICABLE
    return APP_NOT_APPLICABLE


def _recommended_approach(strategy):
    tool_sequence = strategy.get("tool_sequence") or []
    if tool_sequence:
        return str(tool_sequence[0])
    description = strategy.get("description")
    if description:
        return str(description)
    return None


def _strategy_rank(strategy):
    """Deterministic sort key used before applicability evaluation."""
    rejected = 1 if strategy.get("deprecated") else 0
    superseded = 1 if strategy.get("superseded_by") else 0
    confidence = -float(strategy.get("confidence") or 0.0)
    return (rejected, superseded, confidence, str(strategy.get("strategy_id")))


def evaluate_candidate(strategy, situation, context_id=None):
    """Evaluate ONE strategy deterministically.

    ``strategy`` is a plain dict (``Strategy.to_dict()`` shape). Returns a
    machine-readable candidate application result.
    """
    strategy_id = strategy.get("strategy_id")
    if not strategy_id:
        raise ValueError("strategy candidates need a strategy_id")
    rationale = None

    if strategy.get("deprecated"):
        status = APP_DEPRECATED
        rationale = "strategy %s is deprecated and is never applicable" % (
            strategy_id,)
    elif strategy.get("superseded_by"):
        status = APP_SUPERSEDED
        rationale = ("strategy %s is superseded by %s and is not applicable"
                     % (strategy_id, strategy.get("superseded_by")))
    else:
        relation = _pattern_relation(strategy.get("problem_class"), situation)
        ctx_state = _context_state(strategy, context_id)
        if relation is False:
            status = APP_NOT_APPLICABLE
            rationale = ("situation pattern %r does not match problem class %r"
                         % (canonical_pattern(situation),
                            canonical_pattern(strategy.get("problem_class"))))
        elif ctx_state == APP_NOT_APPLICABLE:
            status = APP_NOT_APPLICABLE
            rationale = ("context %r is not among the strategy's allowed "
                         "contexts" % (context_id,))
        elif ctx_state == APP_INSUFFICIENT_EVIDENCE:
            status = APP_INSUFFICIENT_EVIDENCE
            rationale = ("strategy restricts applicability to %s but no "
                         "current context was provided" % (
                             ", ".join(sorted(_allowed_contexts(strategy))),))
        elif relation is None:
            status = APP_UNKNOWN
            rationale = ("cannot evaluate whether situation %r fits problem "
                         "class %r" % (canonical_pattern(situation),
                                       canonical_pattern(
                                           strategy.get("problem_class"))))
        else:
            status = APP_APPLICABLE
            rationale = "strategy fits the situation and its context restrictions"

    constraints = strategy.get("constraints") or {}
    supporting_experience_ids = sorted(str(x) for x in (
        constraints.get("supporting_experience_ids") or ()))
    supporting_evidence_ids = sorted(str(x) for x in (
        constraints.get("supporting_evidence_ids") or ()))
    return {
        "strategy_id": strategy_id,
        "strategy_status": status,
        "problem_class": strategy.get("problem_class"),
        "context_id": context_id,
        "recommended_approach": _recommended_approach(strategy),
        "confidence": float(strategy.get("confidence") or 0.0),
        "supporting_experience_ids": supporting_experience_ids,
        "supporting_evidence_ids": supporting_evidence_ids,
        "deprecated": bool(strategy.get("deprecated")),
        "superseded_by": strategy.get("superseded_by"),
        "rationale": rationale,
    }


def apply_strategies(situation, strategies, context_id=None,
                     max_candidates=DEFAULT_MAX_CANDIDATES):
    """Deterministically evaluate a bounded set of strategy candidates.

    Returns an applicability result with a single machine-readable status.
    """
    situation = situation if isinstance(situation, str) else str(situation)
    ordered = sorted(strategies or (), key=_strategy_rank)
    limit = int(max_candidates or DEFAULT_MAX_CANDIDATES)
    truncated = len(ordered) > limit
    bounded = ordered[:limit]

    evaluated = [evaluate_candidate(st, situation, context_id=context_id)
                 for st in bounded]

    supported = [c for c in evaluated
                 if c["strategy_status"] == APP_APPLICABLE]
    insufficient = [c for c in evaluated
                    if c["strategy_status"] == APP_INSUFFICIENT_EVIDENCE]
    unknown = [c for c in evaluated
               if c["strategy_status"] == APP_UNKNOWN]
    deprecated = [c for c in evaluated
                  if c["strategy_status"] == APP_DEPRECATED]
    superseded = [c for c in evaluated
                  if c["strategy_status"] == APP_SUPERSEDED]
    not_applicable = [c for c in evaluated
                      if c["strategy_status"] == APP_NOT_APPLICABLE]

    uncertainties = []
    strategy_ids = sorted(c["strategy_id"] for c in evaluated)

    if not evaluated:
        status = APP_NO_STRATEGY
        rationale = "no strategy candidates were available for evaluation"
        strategy_id = None
        application = None
    elif len(supported) > 1:
        status = APP_CONFLICTING
        rationale = ("multiple strategies are equally applicable: %s"
                     % ", ".join(c["strategy_id"] for c in supported))
        strategy_id = None
        application = None
        uncertainties.append(
            "two or more strategies apply and conflict; no arbitrary "
            "selection is made")
    elif len(supported) == 1:
        status = APP_APPLICABLE
        application = supported[0]
        strategy_id = application["strategy_id"]
        rationale = application["rationale"]
    elif insufficient:
        status = APP_INSUFFICIENT_EVIDENCE
        rationale = ("strategy fit requires a current context that was not "
                     "provided (%s)" % ", ".join(
                         c["strategy_id"] for c in insufficient))
        strategy_id = None
        application = None
        uncertainties.append(
            "current context is missing for strategies that restrict "
            "applicability")
    elif unknown:
        status = APP_UNKNOWN
        rationale = ("situation does not clearly match any candidate problem "
                     "class; applicability cannot be established")
        strategy_id = None
        application = None
        uncertainties.append(
            "problem-class fit could not be evaluated for %s"
            % ", ".join(c["strategy_id"] for c in unknown))
    elif deprecated:
        status = APP_DEPRECATED
        rationale = "only deprecated strategies were available"
        strategy_id = None
        application = None
    elif superseded:
        status = APP_SUPERSEDED
        rationale = "only superseded strategies were available"
        strategy_id = None
        application = None
    else:
        status = APP_NOT_APPLICABLE
        rationale = "no strategy fits the situation or current context"
        strategy_id = None
        application = None

    supporting_experience_ids = []
    supporting_evidence_ids = []
    for c in evaluated:
        for eid in c["supporting_experience_ids"]:
            if eid not in supporting_experience_ids:
                supporting_experience_ids.append(eid)
        for eid in c["supporting_evidence_ids"]:
            if eid not in supporting_evidence_ids:
                supporting_evidence_ids.append(eid)

    return {
        "application_id": derive_application_id(
            situation, strategy_ids, context_id=context_id),
        "status": status,
        "situation": situation,
        "situation_pattern": canonical_pattern(situation),
        "context_id": context_id,
        "strategy_id": strategy_id,
        "application": application,
        "candidates": evaluated,
        "applicable_count": len(supported),
        "candidate_count": len(evaluated),
        "max_candidates": limit,
        "truncated": truncated,
        "rationale": rationale,
        "confidence": float(application["confidence"]
                            if application is not None else 0.0),
        "uncertainties": uncertainties,
        "provenance": {
            "strategy_ids": strategy_ids,
            "supporting_experience_ids": supporting_experience_ids,
            "supporting_evidence_ids": supporting_evidence_ids,
            "context_id": context_id,
        },
    }


def valid_statuses():
    """The canonical machine-readable application status vocabulary."""
    return list(_ALL_STATUSES)