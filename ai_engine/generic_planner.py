"""Generic Deterministic Planner — domain-neutral (Phase 14).

Small, deterministic, serializable planning abstraction for the generic
Persistent Intelligence System. Coding is one domain/plugin, not core.

Concepts:
    situation/problem — what happened / what is being asked
    objective        — what the user wants to achieve (may be empty)
    available_information — Memory recall results (knowledge + experience)
    constraints      — {max_steps, allow_write, require_approval, etc.}
    candidate_actions — list of possible next steps (tool + inputs + rationale)
    selected_action/next_step — chosen candidate or None
    rationale/reason — why this decision
    confidence/status — float / string
    required_authority/approval — bool
    expected_outcome — string

All decisions are deterministic for identical inputs, no LLM, no network,
no side effects, no database access, stdlib-only, serializable.

Six generic rules (justified from existing behavior):
    1. insufficient_information → request/identify missing information
    2. known fact with no action required → no action
    3. clear next step → propose that step
    4. conflicting information → surface conflict
    5. action requiring authority → require approval
    6. failed previous attempt → choose different/safer next step via experience
"""

import hashlib
import json

# Allowed generic tools (domain-neutral, not file.* etc. — file tools are code domain)
GENERIC_ALLOWED_TOOLS = {
    "memory.recall",
    "memory.remember",
    "memory.get",
    "memory.provenance",
    "context.get",
    "context.diff",
    "noop",  # explicit no-action
}

# Tools that require authority even in generic domain (e.g., remembering sensitive)
GENERIC_AUTHORITY_TOOLS = {
    "memory.remember",
}

def _canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)

def _stable_id(*parts):
    joined = "|".join(str(p) for p in parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:12]

def _has_conflicting_knowledge(available_information):
    """Check for conflicting information in available_information.

    Detects:
        - reasoning contradictions (if present)
        - knowledge nodes with same id but different description (rare, but deterministic)
        - explicit conflict flag in available_information
    """
    if not isinstance(available_information, dict):
        return False, None
    # Check explicit contradictions from reasoning
    contradictions = available_information.get("contradictions") or []
    if contradictions:
        return True, f"reasoning contradictions: {len(contradictions)}"
    # Check knowledge nodes for duplicate ids with different content
    knowledge = available_information.get("knowledge") or []
    seen = {}
    for n in knowledge:
        nid = n.get("id")
        if not nid:
            continue
        desc = n.get("description", "")
        if nid in seen and seen[nid] != desc:
            return True, f"conflicting descriptions for {nid!r}"
        seen[nid] = desc
    return False, None

def _has_failed_experience(experiences):
    """Check if past experiences contain a failure for same situation."""
    if not experiences:
        return False, None
    for exp in experiences:
        # Experience summary may contain outcome
        summary = exp.get("summary") if isinstance(exp, dict) else getattr(exp, "summary", None)
        if isinstance(summary, dict) and summary.get("outcome") == "failure":
            return True, summary
        # Also check outcome classification
        outcome = exp.get("outcome") if isinstance(exp, dict) else None
        if outcome == "failure":
            return True, exp
    return False, None


def plan_generic(situation, objective=None, available_information=None, constraints=None, context=None):
    """Deterministic generic planning.

    Args:
        situation: dict or str — problem description (e.g. {"problem": "need fact X"})
        objective: str or None — what to achieve (e.g. "answer question")
        available_information: dict — from Memory recall + experience (e.g. {"knowledge": [...], "experience": [...], "query_terms": [...]})
        constraints: dict — e.g. {"allow_write": False, "max_steps": 1}
        context: ContextSnapshot or dict — for authority checks

    Returns dict (serializable):
        {
            "status": "ok" | "insufficient_information" | "conflict" | "no_action" | "requires_approval",
            "situation": ...,
            "objective": ...,
            "candidate_actions": [...],
            "selected_action": {...} | None,
            "next_step": {...} | None,  # alias for selected_action
            "rationale": str,
            "confidence": float,
            "required_authority": bool,
            "expected_outcome": str,
            "plan_id": str,
        }
    No side effects, no DB, no LLM.
    """
    situation = situation or {}
    if isinstance(situation, str):
        situation = {"problem": situation}
    if not isinstance(situation, dict):
        situation = {"problem": str(situation)}
    objective = objective or situation.get("objective") or ""
    available_information = available_information or {}
    constraints = constraints or {}
    context = context or {}

    # Normalize available_information
    knowledge = available_information.get("knowledge", []) if isinstance(available_information, dict) else []
    experiences = available_information.get("experience", []) if isinstance(available_information, dict) else []
    query_terms = available_information.get("query_terms", []) if isinstance(available_information, dict) else []

    # Deterministic plan_id from situation + objective + knowledge ids
    plan_id = "plan_" + _stable_id(_canonical(situation), _canonical(objective), _canonical([k.get("id") for k in knowledge[:3]]))

    # Rule 4: conflicting information → surface conflict
    has_conflict, conflict_reason = _has_conflicting_knowledge(available_information)
    if has_conflict:
        return {
            "status": "conflict",
            "situation": situation,
            "objective": objective,
            "candidate_actions": [],
            "selected_action": None,
            "next_step": None,
            "rationale": f"conflicting information: {conflict_reason}",
            "confidence": 0.0,
            "required_authority": False,
            "expected_outcome": "need conflict resolution",
            "plan_id": plan_id,
        }

    # Rule 1: insufficient information → request missing (no fabricated plan)
    if not knowledge and not query_terms and objective:
        problem = situation.get("problem", "")
        rationale = f"missing information for: {problem}" if problem else "no available information for objective"
        return {
            "status": "insufficient_information",
            "situation": situation,
            "objective": objective,
            "candidate_actions": [],
            "selected_action": None,
            "next_step": None,
            "rationale": rationale,
            "confidence": 0.0,
            "required_authority": False,
            "expected_outcome": "request missing information",
            "plan_id": plan_id,
        }

    # Rule 6: failed previous attempt → use experience to choose different/safer
    has_failed, failed_info = _has_failed_experience(experiences)
    if has_failed:
        candidate = {"tool": "memory.recall", "inputs": {"query": situation.get("problem", "") or " alternative"}, "rationale": "previous attempt failed, try alternative recall"}
        prev_query = ""
        if experiences and isinstance(experiences[0], dict):
            prev_summary = experiences[0].get("summary", {})
            prev_query = prev_summary.get("query", "") or ""
        if prev_query and candidate["inputs"]["query"] == prev_query:
            candidate["inputs"]["query"] = "alternative " + candidate["inputs"]["query"]
        return {
            "status": "ok",
            "situation": situation,
            "objective": objective,
            "candidate_actions": [candidate],
            "selected_action": candidate,
            "next_step": candidate,
            "rationale": f"previous attempt failed ({failed_info}), trying safer alternative",
            "confidence": 0.4,
            "required_authority": False,
            "expected_outcome": "alternative attempt",
            "plan_id": plan_id,
        }

    # Planner integration: consume learned strategies when available (Phase 15)
    # Strategies are in available_information["strategies"] or ["strategy_candidates"]
    strategies = available_information.get("strategies") or available_information.get("strategy_candidates") or []
    if strategies:
        # Find a strategy whose situation matches current situation's problem
        problem = situation.get("problem", "").lower()
        for strat in strategies:
            strat_sit = ""
            if isinstance(strat, dict):
                strat_sit = (strat.get("situation") or strat.get("problem_class") or "").lower()
                if not strat_sit:
                    strat_sit = (strat.get("description") or "").lower()
            # Simple deterministic match: if problem substring in strat situation or vice versa
            if problem and strat_sit and (problem in strat_sit or strat_sit in problem):
                # Check if current evidence conflicts with strategy
                # If current knowledge contradicts strategy's supporting evidence, surface conflict instead of blindly following
                # For now, propose the strategy's recommended approach
                approach = strat.get("approach") or strat.get("tool_sequence") or ["memory.recall"]
                if isinstance(approach, list):
                    tool = approach[0] if approach else "memory.recall"
                else:
                    tool = str(approach)
                # Ensure tool is in allowed generic tools
                if tool not in GENERIC_ALLOWED_TOOLS:
                    tool = "memory.recall"
                candidate = {"tool": tool, "inputs": {"query": problem or strat_sit}, "rationale": f"learned strategy {strat.get('strategy_id', 'unknown')} previously succeeded"}
                return {
                    "status": "ok",
                    "situation": situation,
                    "objective": objective,
                    "candidate_actions": [candidate],
                    "selected_action": candidate,
                    "next_step": candidate,
                    "rationale": f"using learned strategy for {situation.get('problem')}",
                    "confidence": float(strat.get("confidence", 0.6)),
                    "required_authority": False,
                    "expected_outcome": "apply learned strategy",
                    "plan_id": plan_id,
                }

    # Rule 2: known fact with no action required → no action
    # If objective is empty or explicitly "no action", and we have a clear fact, propose noop
    if not objective or objective.lower() in ("no action", "lookup", "fact_check"):
        if knowledge:
            # If we have a single fact that directly answers, no action needed
            top = knowledge[0]
            # Check if top fact's description contains objective or situation problem
            problem = situation.get("problem", "").lower()
            if problem and problem in top.get("description", "").lower():
                candidate = {"tool": "noop", "inputs": {}, "rationale": "fact already known, no action required"}
                return {
                    "status": "no_action",
                    "situation": situation,
                    "objective": objective,
                    "candidate_actions": [candidate],
                    "selected_action": candidate,
                    "next_step": candidate,
                    "rationale": f"known fact {top.get('id')} already satisfies objective",
                    "confidence": 0.9,
                    "required_authority": False,
                    "expected_outcome": "no action needed",
                    "plan_id": plan_id,
                }

    # Rule 3: clear next step → propose that step
    # If we have knowledge with a procedure or clear next step, propose it
    # For generic, the clear next step is often a recall or remember
    # Determine candidate based on situation and available knowledge
    candidates = []
    if knowledge:
        # Propose recall as clear next step if objective is to find something
        if objective and "recall" in objective.lower():
            candidates.append({"tool": "memory.recall", "inputs": {"query": objective}, "rationale": "objective requires recall"})
        # Propose remember if situation is to remember
        elif situation.get("problem", "").lower().startswith("remember"):
            candidates.append({"tool": "memory.remember", "inputs": {"payload": {"text": situation.get("problem", "")}}, "rationale": "situation requires remembering"})
        else:
            # Generic clear next step: use top knowledge's type to decide
            top = knowledge[0]
            t = top.get("type", "")
            if t in ("procedure", "fact", "observation"):
                candidates.append({"tool": "memory.recall", "inputs": {"query": top.get("name", "") or top.get("id", "")}, "rationale": f"clear next step from {t} {top.get('id')}"})
            else:
                candidates.append({"tool": "memory.recall", "inputs": {"query": situation.get("problem", "") or top.get("id", "")}, "rationale": "clear next step from available knowledge"})
    else:
        # No knowledge but have query_terms: propose recall
        if query_terms:
            candidates.append({"tool": "memory.recall", "inputs": {"query": " ".join(query_terms[:3])}, "rationale": "no knowledge, try recall with query terms"})
        else:
            # Fallback: insufficient
            return {
                "status": "insufficient_information",
                "situation": situation,
                "objective": objective,
                "candidate_actions": [],
                "selected_action": None,
                "next_step": None,
                "rationale": "no available information to propose next step",
                "confidence": 0.0,
                "required_authority": False,
                "expected_outcome": "need more information",
                "plan_id": plan_id,
            }

    if not candidates:
        return {
            "status": "insufficient_information",
            "situation": situation,
            "objective": objective,
            "candidate_actions": [],
            "selected_action": None,
            "next_step": None,
            "rationale": "no candidate actions generated",
            "confidence": 0.0,
            "required_authority": False,
            "expected_outcome": "need more information",
            "plan_id": plan_id,
        }

    # Deterministic selection: first candidate (sorted by tool name for determinism)
    candidates_sorted = sorted(candidates, key=lambda c: c["tool"])
    selected = candidates_sorted[0]

    # Rule 5: action requiring authority → require approval (but status remains ok, authority flagged separately)
    requires_auth = selected["tool"] in GENERIC_AUTHORITY_TOOLS
    if constraints.get("require_approval"):
        requires_auth = True
    if selected["tool"] == "memory.remember" and constraints.get("allow_write") is False:
        requires_auth = True

    # Status is ok for any actionable plan; authority is separate flag
    status = "ok"
    expected = "awaiting approval" if requires_auth else "execute next step"

    return {
        "status": status,
        "situation": situation,
        "objective": objective,
        "candidate_actions": candidates_sorted,
        "selected_action": selected,
        "next_step": selected,
        "rationale": selected.get("rationale", "clear next step"),
        "confidence": 0.7 if not requires_auth else 0.5,
        "required_authority": requires_auth,
        "expected_outcome": expected,
        "plan_id": plan_id,
    }
