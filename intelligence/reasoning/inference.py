"""Inference rules (Phase 6).

Deterministic rules that turn knowledge nodes, experience records, and context
into conclusions with evidence chains:

* knowledge support  -- an applicable knowledge node supports a conclusion.
* experience aggregation -- repeated successful experience raises support.
* generalization (R5) -- a pattern seen across contexts supports a broader,
  conservative, context-qualified conclusion.

All scoring functions are pure and deterministic.
"""

from .evidence_chain import build_chain
from .types import Conclusion, EvidenceStep

# Token relevance: how strongly a knowledge node relates to a reasoning query.
_TOKEN_STOP = frozenset({"the", "a", "an", "is", "of", "for", "this",
                         "that", "in", "to", "how", "what", "should",
                         "strategy", "be", "used", "task"})


def _tokens(*values):
    merged = set()
    for value in values:
        if not value:
            continue
        text = str(value).lower()
        for token in text.replace("-", " ").replace("_", " ").split():
            token = token.strip(".,:;()[]{}\"'")
            if token and token not in _TOKEN_STOP:
                merged.add(token)
    return merged


def _query_tokens(query):
    target = query.target or {}
    return _tokens(
        query.question, query.task_type, query.domain,
        target.get("error"), target.get("file"), target.get("symbol"))


def knowledge_relevance(node, query):
    """Deterministic relevance score (0..1) for a knowledge node vs a query.

    A node is strongly relevant (1.0) if its claim/subject/type matches the
    query's task_type, domain, or target error/symbol/file tokens; otherwise a
    graded token-overlap score; 0.0 if no overlap at all.
    """
    if not isinstance(node, dict):
        return 0.0
    claim_tokens = _tokens(_claim_of(node))
    subject_tokens = _tokens(node.get("subject"))
    ntype_tokens = _tokens(node.get("type"))
    node_tokens = claim_tokens | subject_tokens | ntype_tokens

    target = query.target or {}
    strong_values = [query.task_type, query.domain,
                     target.get("error"), target.get("symbol")]
    strong_tokens = _tokens(*strong_values)
    if strong_tokens & node_tokens:
        return 1.0

    q_tokens = _query_tokens(query)
    if not q_tokens or not node_tokens:
        return 0.0
    overlap = len(q_tokens & node_tokens)
    return round(overlap / max(1, len(q_tokens)), 6)


def _claim_of(node):
    return (node.get("claim") or node.get("name")
            or node.get("description") or "")


def knowledge_confidence_of(node):
    """Read stored confidence from a flattened knowledge node."""
    try:
        value = float((node.get("lifecycle") or {}).get("confidence", 0.0))
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(1.0, value))


def knowledge_support_steps(query, nodes):
    """Return supported EvidenceSteps for applicable knowledge nodes."""
    steps = []
    for node in nodes:
        relevance = knowledge_relevance(node, query)
        if relevance <= 0.0:
            continue
        conf = round(knowledge_confidence_of(node) * (0.5 + 0.5 * relevance),
                     6)
        if conf <= 0.0:
            continue
        steps.append(EvidenceStep(
            node.get("id"), "knowledge", _claim_of(node), conf,
            f"knowledge relevance={relevance}"))
    return steps


def experience_aggregation(query, experiences, context_match=1.0):
    """Aggregate experience support for the query's task_type.

    Returns (steps, sample_count, success_rate). Only experiences matching the
    task_type (and domain) are considered. Only *successful* experiences
    contribute supporting steps; failures are counted for the success rate but
    do not lend positive support.
    """
    task_type = query.task_type
    relevant = [e for e in experiences
                if getattr(e, "task_type", None) == task_type]
    domain = query.domain
    if domain:
        relevant = [e for e in relevant
                    if not getattr(e, "domain", None)
                    or getattr(e, "domain") == domain]

    def _ok(e):
        summary = getattr(e, "summary", None) or {}
        return str(summary.get("outcome", "")).lower() in (
            "success", "succeeded", "success_verified", "ok", "pass")

    if not relevant:
        return [], 0, 0.0

    sample_count = len(relevant)
    successes = sum(1 for e in relevant if _ok(e))
    success_rate = round(successes / sample_count, 6)

    steps = []
    seen_ids = set()
    for e in sorted(relevant, key=lambda x: getattr(x, "experience_id", "")):
        eid = getattr(e, "experience_id", None) or str(id(e))
        if eid in seen_ids:
            continue
        seen_ids.add(eid)
        if not _ok(e):
            continue  # failures never lend positive support
        per_match = (getattr(e, "summary", None) or {}).get(
            "context_match", context_match)
        try:
            per_match = float(per_match)
        except (TypeError, ValueError):
            per_match = context_match
        per_match = max(0.0, min(1.0, per_match))
        conf = round(per_match * 0.9, 6)
        if conf <= 0.0:
            continue
        steps.append(EvidenceStep(
            eid, "experience",
            f"{task_type} outcome={'success'}",
            conf, f"context_match={per_match}"))
    return steps, sample_count, success_rate


def conservative_generalization(query, context_id, experiences,
                                context_match=1.0):
    """R5: conservative, context-qualified generalization.

    If the same task_type has been attempted in multiple distinct contexts with
    consistent success, propose a low-confidence, context-qualified conclusion
    (never a universal claim).
    """
    task_records = [e for e in experiences
                    if getattr(e, "task_type", None) == query.task_type]
    distinct_contexts = {getattr(e, "context_id", None) or ""
                         for e in task_records}
    distinct_contexts.discard("")
    if len(distinct_contexts) < 2:
        return None

    def _ok(e):
        summary = getattr(e, "summary", None) or {}
        return str(summary.get("outcome", "")).lower() in (
            "success", "succeeded", "success_verified", "ok", "pass")

    successes = sum(1 for e in task_records if _ok(e))
    if successes < len(task_records):
        return None
    # Conservative: low initial confidence, context-qualified (R5).
    conf = round(0.5 * context_match * min(1.0,
                                           len(distinct_contexts) / 3.0), 6)
    step = EvidenceStep("generalization", "experience",
                        f"{query.task_type} generalizes across "
                        f"{len(distinct_contexts)} contexts",
                        conf, "context-qualified generalization")
    chain = build_chain([step])
    return Conclusion(
        claim=f"{query.task_type} is broadly applicable across similar "
              f"contexts (context-qualified)",
        confidence=chain.propagated_confidence, evidence_chain=chain,
        context_id=context_id, source="generalization")
