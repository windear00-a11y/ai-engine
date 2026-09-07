"""Canonical, deterministic Decision (Phase 28).

Pure domain module: stdlib only, no database, no network, no LLM.

Reasoning determines what the available information supports; DECISION selects
an intended course under explicit constraints. A decision never executes
anything and never invents certainty: it may legitimately return
UNKNOWN / INSUFFICIENT_EVIDENCE instead of fabricating confidence.
"""

import hashlib
import json

DC_DECIDED = "decided"
DC_AMBIGUOUS = "ambiguous"
DC_CONFLICT = "conflict"
DC_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
DC_UNKNOWN = "unknown"


def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      default=str)


def canonical_pattern(value):
    if not isinstance(value, str):
        value = str(value)
    return " ".join(value.strip().lower().split())


def derive_decision_id(reasoning_id, selected_option_id, constraints=None):
    """Deterministic decision identity over the reasoning reference."""
    digest = hashlib.sha256(_canonical({
        "reasoning_id": reasoning_id,
        "selected_option_id": selected_option_id,
        "constraints": constraints or {},
    }).encode("utf-8")).hexdigest()
    return "dc_" + digest[:32]


def derive_option_id(course, source, index):
    """Deterministic option id (stable under identical inputs)."""
    digest = hashlib.sha256(_canonical({
        "course": course, "source": source, "index": index,
    }).encode("utf-8")).hexdigest()
    return "opt_" + digest[:16]


def _matches_constraint(course, allowed=None, disallowed=None):
    pattern = canonical_pattern(course)
    if allowed:
        allowed_patterns = [canonical_pattern(x) for x in allowed]
        if pattern not in allowed_patterns:
            return False
    if disallowed:
        disallowed_patterns = [canonical_pattern(x) for x in disallowed]
        if pattern in disallowed_patterns:
            return False
    return True


def make_decision(reasoning, constraints=None, alternatives=None):
    """Select an intended course under constraints, deterministically.

    ``reasoning`` is a ReasoningResult dict (ai_engine.reasoning). Options are
    derived from the reasoning recommendation plus any explicitly provided
    ``alternatives`` (list of dicts with ``course``).
    """
    reasoning = dict(reasoning or {})
    constraints = constraints or {}
    reasoning_id = reasoning.get("reasoning_id")
    status_in = reasoning.get("status")

    alternatives = list(alternatives or ())
    ordered_alternatives = []
    seen = set()
    for index, item in enumerate(alternatives):
        course = item.get("course") if isinstance(item, dict) else item
        if not course:
            continue
        key = canonical_pattern(str(course))
        if key in seen:
            continue
        seen.add(key)
        ordered_alternatives.append({
            "option_id": item.get("option_id") if isinstance(item, dict)
            else None,
            "course": str(course),
            "rationale": item.get("rationale") if isinstance(item, dict)
            else None,
            "source": item.get("source") if isinstance(item, dict)
            else "provided",
            "index": index,
        })

    reasoning_course = reasoning.get("recommended_approach")
    options = []
    if reasoning_course:
        options.append({
            "option_id": derive_option_id(reasoning_course, "reasoning", 0),
            "course": str(reasoning_course),
            "rationale": reasoning.get("recommendation"),
            "source": "reasoning",
            "index": 0,
        })
    for alt in ordered_alternatives:
        if alt["option_id"] is None:
            alt["option_id"] = derive_option_id(alt["course"],
                                                alt["source"], alt["index"])
        options.append(alt)

    allowed = constraints.get("allowed_approaches") or []
    disallowed = constraints.get("disallowed_approaches") or []
    if allowed or disallowed:
        options = [o for o in options
                   if _matches_constraint(o["course"], allowed=allowed,
                                          disallowed=disallowed)]

    strategy_id = reasoning.get("applicable_strategy_id")
    strategy_application_id = reasoning.get("strategy_application_id")
    provenance = {
        "reasoning_id": reasoning_id,
        "applicable_strategy_id": strategy_id,
        "strategy_application_id": strategy_application_id,
        "context_id": reasoning.get("context_id"),
    }

    if status_in in ("insufficient_information",):
        status = DC_INSUFFICIENT_EVIDENCE
        selected = None
        rationale = ("reasoning found insufficient information; no course is "
                     "selected")
    elif status_in == "unknown":
        status = DC_UNKNOWN
        selected = None
        rationale = ("reasoning could not establish what the information "
                     "supports; decision is explicitly unknown")
    elif status_in == "conflict":
        status = DC_CONFLICT
        selected = None
        rationale = ("reasoning identified conflicting inputs; no course is "
                     "selected until the conflict is resolved")
    elif status_in == "supported":
        if not options:
            status = DC_INSUFFICIENT_EVIDENCE
            selected = None
            rationale = ("reasoning supports a course but every known option "
                         "was eliminated by constraints or absent")
        elif len(options) == 1:
            status = DC_DECIDED
            selected = options[0]
            rationale = selected.get("rationale") or (
                "selected the only supported course")
        else:
            status = DC_AMBIGUOUS
            selected = None
            rationale = ("multiple compatible courses remain; selection is "
                         "ambiguous")
    else:
        status = DC_UNKNOWN
        selected = None
        rationale = "reasoning status is not recognized; decision is unknown"

    option_ids = sorted(o["option_id"] for o in options)
    selected_option_id = selected["option_id"] if selected else None
    decision_id = derive_decision_id(reasoning_id, selected_option_id,
                                     constraints)

    return {
        "decision_id": decision_id,
        "status": status,
        "selected_course": selected["course"] if selected else None,
        "selected_option": selected_option_id,
        "selected_option_id": selected_option_id,
        "option_ids": option_ids,
        "rationale": rationale,
        "alternatives": options,
        "constraints": constraints,
        "reasoning_id": reasoning_id,
        "applicable_strategy_id": strategy_id,
        "strategy_application_id": strategy_application_id,
        "confidence": reasoning.get("confidence"),
        "explicit_unknown": status in (DC_UNKNOWN, DC_INSUFFICIENT_EVIDENCE),
        "provenance": provenance,
    }