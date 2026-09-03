"""Main decision engine (Phase 7).

Maps reasoning outputs to a selected strategy while respecting policy, risk,
context, and authority boundaries. The bridge between "what is true"
(reasoning) and "what should we do" (planning).

Flow (D1-D8):
  D1  consider candidate strategies
  D2  filter by context applicability
  D3  score = confidence * context_match * evidence_quality * recency
  D4  risk assessment (low/medium/high)
  D5  policy filtering (block policy-violating candidates)
  D6  authority boundaries (low -> autonomous; medium/high -> approval)
  D7  human guidance when no candidate meets the threshold
  D8  audit trail (record every decision, including blocked/guidance)
"""

from .context import strategy_context_applicable, context_match, context_id_of
from .policies import PolicyEngine, default_policies_dir
from .risk import infer_risk_from_tools, apply_risk_policy
from .schema import derive_decision_id
from .scorer import effective_score
from .types import CandidateStrategy, Decision, RiskLevel

MIN_CONFIDENCE = 0.35


class _CtxStr:
    def __init__(self, strategy_id, name, problem_class, tool_sequence,
                 confidence, context_restrictions=None):
        self.strategy_id = strategy_id
        self.name = name
        self.problem_class = problem_class
        self.tool_sequence = list(tool_sequence or ())
        self.confidence = float(confidence)
        self.context_restrictions = dict(context_restrictions or {})


def _attr(obj, name, default=None):
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _coerce_strategy(s):
    if isinstance(s, _CtxStr):
        return s
    sid = _attr(s, "strategy_id")
    if sid is None:
        raise ValueError("candidate strategy missing strategy_id")
    return _CtxStr(
        strategy_id=sid,
        name=_attr(s, "name", sid),
        problem_class=_attr(s, "problem_class", ""),
        tool_sequence=_attr(s, "tool_sequence", []),
        confidence=_attr(s, "confidence", 0.0),
        context_restrictions=_attr(s, "context_restrictions", None),
    )


def _decision_confidence(reasoning_output, candidates):
    if reasoning_output is not None:
        conclusions = _attr(reasoning_output, "conclusions", None) or []
        if conclusions:
            return float(_attr(conclusions[0], "confidence", 0.0) or 0.0)
    if candidates:
        return max(float(c.strategy_confidence) for c in candidates)
    return 0.0


def _evidence_quality(reasoning_output):
    if reasoning_output is None:
        return 1.0
    conclusions = _attr(reasoning_output, "conclusions", None) or []
    if not conclusions:
        return 1.0
    chain = _attr(conclusions[0], "evidence_chain", None)
    if chain is None:
        return 1.0
    return round(float(_attr(chain, "chain_quality", 1.0) or 1.0), 6)


def _top_conclusion_context_match(reasoning_output, context):
    if reasoning_output is None:
        return 1.0
    conclusions = _attr(reasoning_output, "conclusions", None) or []
    if not conclusions:
        return 1.0
    cctx = _attr(conclusions[0], "context_id", None) or ""
    if not cctx:
        return 1.0
    return context_match(context, cctx)


def _chain_dict(reasoning_output):
    if reasoning_output is None:
        return {}
    conclusions = _attr(reasoning_output, "conclusions", None) or []
    if not conclusions:
        return {}
    chain = _attr(conclusions[0], "evidence_chain", None)
    if chain is not None and hasattr(chain, "to_dict"):
        return chain.to_dict()
    return {}


def _highest_risk(candidates):
    order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
    if not candidates:
        return RiskLevel.LOW
    return max((c.risk_level for c in candidates),
               key=lambda r: order.get(r, 0))


def build_candidates(candidate_strategies, context, reasoning_output):
    candidates = []
    rejected_ctx = []
    for raw in candidate_strategies or []:
        s = _coerce_strategy(raw)
        if not strategy_context_applicable(s, context):
            rejected_ctx.append(s.strategy_id)
            continue
        candidates.append(s)
    return candidates, rejected_ctx


def decide(task_id, task_type, candidate_strategies=None, reasoning_output=None,
           context=None, confidence=None, risk_level=None,
           policy_engine=None, recency_weight=1.0, min_confidence=MIN_CONFIDENCE,
           store=None, created_at_epoch=0.0, policies_dir=None):
    """Select the best strategy for task_id.

    Deterministic for identical inputs. Records the decision (including
    blocked and guidance decisions) in the append-only store when provided.

    Parameters
    ----------
    task_id, task_type : identifiers for the task.
    candidate_strategies : list of Strategy-like objects. When None, the
        registry is queried for ``task_type`` (problem_class).
    reasoning_output : ReasoningOutput or None.
    context : dict or ContextSnapshot (must expose ``context_id``).
    policy_engine : PolicyEngine or None (defaults to loading from
        ``intelligence/policies/``).
    min_confidence : minimum decision confidence to select a strategy.
    """
    if policy_engine is None:
        policy_engine = PolicyEngine(
            policies_dir=policies_dir or default_policies_dir())

    # Resolve candidate strategies from registry when not supplied.
    if candidate_strategies is None:
        try:
            from intelligence.strategy.registry import strategies_for_problem_class
            from intelligence.strategy.store import StrategyStore
            s_store = StrategyStore()
            try:
                candidate_strategies = strategies_for_problem_class(
                    task_type, store=s_store)
            finally:
                s_store.close()
        except Exception:
            candidate_strategies = []

    ctx_id = context_id_of(context) or "ctx_unknown"
    reasoning_id = _attr(reasoning_output, "reasoning_id", None) if \
        reasoning_output is not None else None

    raw_candidates, rejected_ctx = build_candidates(
        candidate_strategies, context, reasoning_output)

    decision_conf = confidence
    if decision_conf is None:
        decision_conf = _decision_confidence(reasoning_output, raw_candidates)
    decision_conf = round(float(decision_conf), 6)

    ev_quality = _evidence_quality(reasoning_output)
    ctx_match = _top_conclusion_context_match(reasoning_output, context)

    # Build scored CandidateStrategy list with risk + policy.
    scored = []
    for raw in raw_candidates:
        inferred = infer_risk_from_tools(raw.tool_sequence)
        facts_risk = {
            "mutation": inferred in (RiskLevel.MEDIUM, RiskLevel.HIGH),
            "irreversible": inferred == RiskLevel.HIGH,
        }
        risk = apply_risk_policy(inferred, policy_engine, facts_risk)
        if risk_level is not None:
            risk = risk_level

        decide_facts = {
            "domain": "decide",
            "task_type": task_type,
            "risk_level": risk,
            "confidence": decision_conf,
        }
        blocked, req_approval, _ = policy_engine.check(decide_facts)
        filtered = bool(blocked)
        filter_reason = "policy_violation" if filtered else None
        requires_approval = bool(req_approval) or \
            risk in (RiskLevel.MEDIUM, RiskLevel.HIGH)

        # High-risk candidates blocked by policy are not scored.
        if filtered:
            cand = CandidateStrategy(
                strategy_id=raw.strategy_id,
                name=raw.name,
                problem_class=raw.problem_class,
                tool_sequence=raw.tool_sequence,
                strategy_confidence=raw.confidence,
                context_match=ctx_match,
                evidence_quality=ev_quality,
                recency_weight=recency_weight,
                risk_level=risk,
                score=None,
                filtered=True,
                filter_reason=filter_reason,
                requires_approval=requires_approval,
            )
            scored.append(cand)
            continue

        score = effective_score(raw.confidence, ctx_match, ev_quality,
                                recency_weight, risk)
        cand = CandidateStrategy(
            strategy_id=raw.strategy_id,
            name=raw.name,
            problem_class=raw.problem_class,
            tool_sequence=raw.tool_sequence,
            strategy_confidence=raw.confidence,
            context_match=ctx_match,
            evidence_quality=ev_quality,
            recency_weight=recency_weight,
            risk_level=risk,
            score=score,
            filtered=False,
            filter_reason=None,
            requires_approval=requires_approval,
        )
        scored.append(cand)

    # Select highest effective score among non-filtered where confidence >= threshold.
    eligible = [c for c in scored
                if not c.filtered and c.score is not None
                and decision_conf >= min_confidence]
    eligible.sort(key=lambda c: (-c.score, c.strategy_id))

    if eligible:
        selected = eligible[0]
        alternatives = [
            {"strategy_id": c.strategy_id, "score": c.score}
            for c in eligible[1:]
        ]
        # Also include filtered as rejected alternatives.
        alternatives += [
            {"strategy_id": c.strategy_id, "reason": c.filter_reason}
            for c in scored if c.filtered
        ]
        selected_id = selected.strategy_id
        risk = selected.risk_level
        approval = selected.requires_approval
        status = "selected"
        rationale = {
            "strategy_id": selected.strategy_id,
            "score": selected.score,
            "context_match": selected.context_match,
            "evidence_quality": selected.evidence_quality,
            "recency_weight": recency_weight,
            "reasoning_id": reasoning_id,
            "evidence_chain": _chain_dict(reasoning_output),
        }
    else:
        selected = None
        selected_id = None
        risk = risk_level or _highest_risk(scored) if scored else (
            risk_level or RiskLevel.LOW)
        approval = True
        status = "needs_guidance"
        alternatives = [
            {"strategy_id": c.strategy_id,
             "reason": c.filter_reason or "below_min_confidence"}
            for c in scored
        ]
        rationale = {
            "reasoning_id": reasoning_id,
            "reason": "no_suitable_strategy",
            "rejected_ctx": rejected_ctx,
            "evidence_chain": _chain_dict(reasoning_output),
        }
        if rejected_ctx and not alternatives:
            alternatives = [{"strategy_id": rid, "reason": "context_inapplicable"}
                            for rid in rejected_ctx]

    decision_id = derive_decision_id(task_id, selected_id, reasoning_id,
                                     ctx_id, decision_conf)
    kind = "autonomous" if (selected and not approval) else (
        "guidance" if status == "needs_guidance" else "proposed")

    decision = Decision(
        decision_id=decision_id,
        task_id=task_id,
        task_type=task_type,
        selected_strategy_id=selected_id,
        reasoning_id=reasoning_id,
        confidence=decision_conf,
        rationale=rationale,
        alternatives_rejected=alternatives,
        risk_level=risk,
        approval_required=approval,
        status=status,
        context_id=ctx_id,
        created_at_epoch=created_at_epoch,
        kind=kind,
    )
    if store is not None:
        store.save(decision)
    return decision


__all__ = ["decide", "build_candidates", "MIN_CONFIDENCE"]
