"""Integrate reasoning → decision → plan → act → observe → verify → learn (Phase 9)."""

from intelligence.reasoning.types import ReasoningQuery
from intelligence.decision.types import RiskLevel


def _infer_intent(task):
    # Map task_type to planner intent
    task_type = task.get("task_type") or task.get("intent") or "generic"
    if task_type in ("bug_fix", "test_verify", "generic"):
        return task_type
    # Heuristic: if error present -> bug_fix, if test file -> test_verify
    target = task.get("target") or {}
    if isinstance(target, dict) and target.get("error"):
        return "bug_fix"
    return "generic"


def integrate_reasoning_decision(task, context_snapshot, knowledge_nodes, experiences,
                                 reasoning_store=None, decision_store=None,
                                 strategy_store=None):
    """Run reasoning and decision for the task.

    Returns (reasoning_output, decision). On failure returns (None, None) so
    caller can fallback.
    """
    try:
        from intelligence.reasoning.engine import reason
        query = ReasoningQuery(
            question=task.get("description") or task.get("question") or "What strategy?",
            task_type=task.get("task_type") or _infer_intent(task),
            domain=task.get("domain") or "",
            target=task.get("target") or {},
        )
        # Build context dict from snapshot
        ctx = {"context_id": getattr(context_snapshot, "context_id", None) or "ctx_unknown"}
        # Also pass snapshot's raw dict for richer matching if needed
        if hasattr(context_snapshot, "to_dict"):
            try:
                ctx.update(context_snapshot.to_dict())
            except Exception:
                pass
        reasoning_output = reason(query, ctx, knowledge_nodes=knowledge_nodes,
                                  experiences=experiences, store=reasoning_store,
                                  created_at_epoch=0.0)
    except Exception:
        return None, None

    try:
        from intelligence.decision.engine import decide
        # Candidate strategies: from registry filtered by problem_class
        candidates = None
        if strategy_store is not None:
            try:
                candidates = strategy_store.list_by_problem_class(reasoning_output.query.get("task_type", task.get("task_type", "")))
            except Exception:
                candidates = None
        else:
            try:
                from intelligence.strategy.registry import strategies_for_problem_class
                candidates = strategies_for_problem_class(reasoning_output.query.get("task_type", ""))
            except Exception:
                candidates = []

        decision = decide(
            task_id=task.get("task_id") or task.get("id") or "task_unknown",
            task_type=reasoning_output.query.get("task_type", task.get("task_type", "")),
            candidate_strategies=candidates,
            reasoning_output=reasoning_output,
            context=ctx,
            store=decision_store,
            created_at_epoch=0.0,
        )
        return reasoning_output, decision
    except Exception:
        return reasoning_output, None
