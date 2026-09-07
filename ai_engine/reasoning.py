"""Canonical, deterministic Reasoning (Phase 28).

Pure domain module: stdlib only, no database, no network, no LLM.
Reasoning determines what the available structured information supports. It is
deterministic, side-effect-free, bounded and uncertainty-preserving: missing
evidence is reported as missing and never invented, and a confidence number
never hides uncertainty.

Inputs are structured (situation, context, knowledge, experience, strategies,
evidence, constraints) rather than free-form hidden state, and the result
carries enough information to explain what was considered and what it
supports.
"""

import hashlib
import json

RS_SUPPORTED = "supported"
RS_INSUFFICIENT_INFORMATION = "insufficient_information"
RS_CONFLICT = "conflict"
RS_UNKNOWN = "unknown"

DEFAULT_BOUNDS = {
    "strategy_candidates": 6,
    "knowledge": 12,
    "evidence": 12,
    "experiences": 6,
}


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def canonical_pattern(value):
    if not isinstance(value, str):
        value = str(value)
    return " ".join(value.strip().lower().split())


def _id_of(item, *keys):
    for key in keys:
        value = item.get(key) if isinstance(item, dict) else getattr(
            item, key, None)
        if value is not None:
            return str(value)
    return None


def canonical_bounds(overrides=None):
    bounds = dict(DEFAULT_BOUNDS)
    if isinstance(overrides, dict):
        for key in bounds:
            try:
                value = int(overrides.get(key) or 0)
            except (TypeError, ValueError):
                value = 0
            if value > 0:
                bounds[key] = value
    return bounds


def _truncate_sorted(items, limit):
    """Deterministically bound an input list (sorted by id)."""
    result = []
    for item in items or ():
        key = _sort_key(item)
        if key is None:
            continue
        result.append(item)
    result.sort(key=_sort_key)
    truncated = len(result) > limit
    return result[:limit], truncated


def _sort_key(item):
    return _id_of(item, "strategy_id", "experience_id", "evidence_id", "id",
                  "knowledge_id", "record_id")


def derive_reasoning_id(situation, strategy_application_id, strategy_ids,
                        evidence_ids, context_id, constraints=None,
                        payload=None):
    """Deterministic reasoning identity over every considered input."""
    digest = hashlib.sha256(_canonical({
        "situation": canonical_pattern(situation),
        "strategy_application_id": strategy_application_id,
        "strategy_ids": sorted(str(x) for x in strategy_ids or ()),
        "evidence_ids": sorted(str(x) for x in evidence_ids or ()),
        "context_id": context_id,
        "constraints": constraints or {},
        "payload": payload or {},
    }).encode("utf-8")).hexdigest()
    return "rs_" + digest[:32]


def _application_strategies(application, strategies):
    """The applicable strategies of an application result (deterministic)."""
    if not isinstance(application, dict):
        return []
    applicable_ids = {
        c["strategy_id"]
        for c in (application.get("candidates") or ())
        if c.get("strategy_status") == "applicable"
    }
    selected_id = application.get("strategy_id")
    if selected_id is not None:
        applicable_ids.add(selected_id)
    ordered = [s for s in (strategies or ())
               if _id_of(s, "strategy_id") in applicable_ids]
    ordered.sort(key=_sort_key)
    return ordered


def _recommendation_from(strategy):
    if strategy is None:
        return None, None
    approach = None
    tool_sequence = strategy.get("tool_sequence") or []
    if tool_sequence:
        approach = str(tool_sequence[0])
    description = strategy.get("description")
    return approach, (approach or description)


def reason(situation, application=None, strategies=None, knowledge=None,
           experiences=None, evidence=None, context_id=None,
           constraints=None, bounds=None):
    """Deterministically reason over structured inputs.

    Returns a ReasoningResult dict with a machine-readable status belonging to
    SUPPORTED / INSUFFICIENT_INFORMATION / CONFLICT / UNKNOWN.
    """
    situation = situation if isinstance(situation, str) else str(situation)
    constraints = constraints or {}
    caps = canonical_bounds(bounds)

    knowledge_b, knowledge_truncated = None, False
    experiences_b, experiences_truncated = None, False
    evidence_b, evidence_truncated = None, False
    strategies_b, strategies_truncated = None, False

    knowledge_b, knowledge_truncated = _truncate_sorted(
        knowledge, caps["knowledge"])
    experiences_b, experiences_truncated = _truncate_sorted(
        experiences, caps["experiences"])
    evidence_b, evidence_truncated = _truncate_sorted(
        evidence, caps["evidence"])
    strategies_b, strategies_truncated = _truncate_sorted(
        strategies, caps["strategy_candidates"])

    truncated = (knowledge_truncated or experiences_truncated
                 or evidence_truncated or strategies_truncated)

    knowledge_ids = [_id_of(n, "id", "knowledge_id") or "" for n in knowledge_b]
    knowledge_ids = [k for k in knowledge_ids if k]
    experience_ids = [e for e in
                      (_id_of(x, "experience_id") for x in experiences_b)
                      if e]
    evidence_ids = [e for e in (_id_of(x, "evidence_id") for x in evidence_b)
                    if e]
    strategy_ids = [s for s in
                    (_id_of(x, "strategy_id") for x in strategies_b) if s]

    conflicts = []
    uncertainties = []

    applicable_strategies = _application_strategies(application, strategies_b)
    if not applicable_strategies and strategies_b:
        # A strategy set may exist with no confirmed applicability: preserve
        # the application's own signal instead of pretending one applies.
        app_status = application.get("status") if isinstance(
            application, dict) else None
        if app_status == "conflicting":
            conflicts.append({
                "type": "conflicting_strategy_application",
                "strategy_ids": sorted((
                    c["strategy_id"] for c in
                    (application.get("candidates") or ())
                    if c.get("strategy_status") == "applicable")),
            })
        elif app_status in ("insufficient_evidence", "unknown",
                            "not_applicable", "superseded", "deprecated"):
            uncertainties.append(
                "strategy application returned %s (no applicable strategy)"
                % (app_status,))

    recommended_approach = None
    recommendation = None
    selected_strategy = None
    if applicable_strategies:
        approaches = sorted(
            {str(x).lower() for x in
             (_recommendation_from(s)[0] for s in applicable_strategies)
             if x})
        if len(approaches) > 1:
            conflicts.append({
                "type": "strategy_disagreement",
                "approaches": approaches,
                "strategy_ids": [s["strategy_id"] for s in
                                 applicable_strategies],
            })
        else:
            selected_strategy = applicable_strategies[0]
            recommended_approach, recommendation = _recommendation_from(
                selected_strategy)

    # Missing evidence referenced by the applicable strategy is surfaced as
    # uncertainty rather than treated as known.
    referenced_evidence = set()
    for s in applicable_strategies or ():
        for eid in (s.get("constraints") or {}).get(
                "supporting_evidence_ids") or ():
            referenced_evidence.add(str(eid))
    missing_evidence = sorted(referenced_evidence - set(evidence_ids))
    if missing_evidence:
        uncertainties.append(
            "evidence referenced by the applicable strategy is not present "
            "among the considered evidence: %s"
            % ", ".join(missing_evidence))

    if conflicts:
        status = RS_CONFLICT
    elif (not applicable_strategies and not evidence_b
          and not knowledge_b and not experiences_b):
        status = RS_INSUFFICIENT_INFORMATION
    elif applicable_strategies:
        if missing_evidence:
            status = RS_INSUFFICIENT_INFORMATION
            recommendation = recommendation or (
                "a strategy applies but its supporting evidence is missing")
        else:
            status = RS_SUPPORTED
    elif evidence_b or knowledge_b or experiences_b:
        status = RS_UNKNOWN
        recommendation = (
            "information is available but no applicable strategy supports a "
            "recommendation; uncertainty is preserved")
    else:
        status = RS_INSUFFICIENT_INFORMATION
        recommendation = "no applicable strategy and no supporting information"

    selected_strategy_id = selected_strategy.get("strategy_id") \
        if selected_strategy else None
    confidence = None
    if status == RS_SUPPORTED and selected_strategy is not None:
        confidence = float(selected_strategy.get("confidence") or 0.0)

    application_id = application.get("application_id") \
        if isinstance(application, dict) else None

    reasoning_id = derive_reasoning_id(
        situation, application_id, strategy_ids, evidence_ids, context_id,
        constraints=constraints)

    return {
        "reasoning_id": reasoning_id,
        "status": status,
        "situation": situation,
        "situation_pattern": canonical_pattern(situation),
        "context_id": context_id,
        "strategy_application_id": application_id,
        "applicable_strategy_id": selected_strategy_id,
        "applicable_strategy_ids": [s["strategy_id"] for s in
                                    applicable_strategies],
        "recommendation": recommendation,
        "recommended_approach": recommended_approach,
        "confidence": confidence,
        "summary": {
            "knowledge": [n.get("description") for n in knowledge_b],
            "experiences": [x.get("summary") if isinstance(x, dict)
                            else None for x in experiences_b],
            "evidence_claims": [e.get("claim") for e in evidence_b],
        },
        "evidence_considered": [e.get("claim") for e in evidence_b],
        "conflicts": conflicts,
        "uncertainties": uncertainties,
        "inputs": {
            "context_id": context_id,
            "knowledge_ids": knowledge_ids,
            "experience_ids": experience_ids,
            "strategy_ids": strategy_ids,
            "evidence_ids": evidence_ids,
            "constraints": constraints,
        },
        "bounds": caps,
        "truncated": truncated,
        "provenance": {
            "strategy_application_id": application_id,
            "applicable_strategy_ids": [s["strategy_id"] for s in
                                        applicable_strategies],
            "evidence_ids": evidence_ids,
            "experience_ids": experience_ids,
            "knowledge_ids": knowledge_ids,
            "context_id": context_id,
        },
    }