"""Main reasoning engine (Phase 6).

Orchestrates deterministic inference from context, knowledge, experience, and
evidence into a :class:`ReasoningOutput`. It is read-only (advisory): it
produces conclusions, contradictions, and insufficiency flags but never takes
action.

The engine accepts the knowledge/experience inputs it reasons over (the caller
retrieves them), so it stays purely deterministic and does not reach into
production stores itself. ``context`` supplies the context_id and applicability
filtering.
"""

from .contradiction import detect_all_contradictions
from .evidence_chain import build_chain
from .inference import (
    conservative_generalization,
    experience_aggregation,
    knowledge_support_steps,
)
from .schema import ReasoningOutput, derive_reasoning_id
from .store import ReasoningStore
from .types import (
    Conclusion,
    EvidenceStep,
    InsufficientEvidence,
    ReasoningQuery,
)


def _context_id_of(context):
    if isinstance(context, dict):
        return context.get("context_id")
    return getattr(context, "context_id", None) or ""


def _context_match_of(context, other_context_id):
    """Deterministic context similarity.

    If other_context_id matches the current context_id exactly, match is 1.0.
    Otherwise fall back to a conservative lower bound.
    """
    current = _context_id_of(context)
    if not current:
        return 0.5
    if other_context_id == current:
        return 1.0
    return 0.5


def _in_context(node, query, context_id):
    """Context applicability filtering for a knowledge node.

    A node restricted to contexts that exclude the current context_id is
    filtered out (Phase 6 context filtering). Invalidated/superseded nodes are
    never used as support.
    """
    lifecycle = (node or {}).get("lifecycle") or {}
    status = lifecycle.get("status", "active")
    if status in ("superseded", "invalidated"):
        return False
    restrictions = lifecycle.get("context_restrictions") or {}
    if not isinstance(restrictions, dict):
        return True
    allowed = restrictions.get("allowed_contexts")
    if isinstance(allowed, (list, tuple)) and allowed:
        if context_id and context_id not in allowed:
            return False
    system = restrictions.get("system")
    if isinstance(system, str) and system and context_id and system != context_id:
        return False
    return True


def _usable_experience(exp, query, context):
    """Context applicability filtering for experience records."""
    task_type = query.task_type
    if task_type and getattr(exp, "task_type", None) != task_type:
        return False
    domain = query.domain
    if domain and getattr(exp, "domain", None) and \
            getattr(exp, "domain") != domain:
        return False
    return True


def reason(query, context, knowledge_nodes=None, experiences=None,
           store=None, created_at_epoch=0.0):
    """Run deterministic reasoning and (optionally) persist the output.

    Returns a :class:`ReasoningOutput`. If ``store`` is provided the output is
    persisted (append-only); otherwise the caller may pass ``store=None`` and
    the output is returned in-memory only.
    """
    if isinstance(query, dict):
        query = ReasoningQuery(**{k: v for k, v in query.items()
                                  if k in ReasoningQuery.__slots__})
    knowledge_nodes = list(knowledge_nodes or [])
    experiences = list(experiences or [])
    context_id = _context_id_of(context) or "ctx_unknown"

    # Context filtering: only applicable, non-lifecycle-excluded inputs.
    applicable_nodes = [n for n in knowledge_nodes
                        if _in_context(n, query, context_id)]
    applicable_experiences = [e for e in experiences
                              if _usable_experience(e, query, context)]

    # Build conclusions.
    conclusions = []
    knowledge_steps = knowledge_support_steps(query, applicable_nodes)

    ctx_match = _context_match_of(context, context_id)
    exp_steps, sample_count, success_rate = experience_aggregation(
        query, applicable_experiences, context_match=ctx_match)

    # 1. Primary combined conclusion: all support steps (knowledge +
    #    successful experience) feed one evidence chain (R2).
    support_steps = list(knowledge_steps) + list(exp_steps)
    if support_steps and (knowledge_steps or success_rate > 0.0):
        chain = build_chain(support_steps)
        evidence_desc = []
        if knowledge_steps:
            evidence_desc.append("knowledge")
        if exp_steps:
            evidence_desc.append(
                f"experience (success {success_rate:.0%}, n={sample_count})")
        conclusions.append(Conclusion(
            claim=(f"{query.task_type} is appropriate"
                   + (f" in domain={query.domain}" if query.domain else "")
                   + f" ({'+'.join(evidence_desc)})"),
            confidence=chain.propagated_confidence, evidence_chain=chain,
            context_id=context_id, source="knowledge"
            if knowledge_steps else "experience"))

    # 2. Conservative, context-qualified generalization (R5).
    gen = conservative_generalization(query, context_id,
                                      applicable_experiences,
                                      context_match=ctx_match)
    if gen is not None:
        conclusions.append(gen)

    # Sort conclusions deterministically by confidence desc, then claim.
    conclusions.sort(key=lambda c: (-c.confidence, c.claim))

    # Contradiction detection (R3): reported over the FULL input corpus, never
    # auto-resolved. Contradictions are properties of the knowledge/experience
    # set regardless of which subset is context-applicable for this query.
    contradictions = detect_all_contradictions(knowledge_nodes, experiences)

    # Insufficient evidence: no usable support at all.
    insufficient = []
    if not knowledge_steps and not exp_steps:
        missing = []
        if not knowledge_steps:
            missing.append("knowledge")
        if not exp_steps:
            missing.append("experience")
        insufficient.append(InsufficientEvidence(
            subject=query.task_type or query.question, missing=missing,
            reason="no applicable knowledge or experience to reason from"))

    reasoning_id = derive_reasoning_id(
        query.to_dict(), context_id, conclusions, contradictions,
        insufficient)
    output = ReasoningOutput(
        reasoning_id=reasoning_id, query=query.to_dict(),
        context_id=context_id, conclusions=conclusions,
        contradictions=contradictions, insufficient_evidence=insufficient,
        created_at_epoch=created_at_epoch)
    if store is not None:
        store.save(output)
    return output
