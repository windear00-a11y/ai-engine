"""Context matching utilities for the decision engine (Phase 7).

Reuses the deterministic, conservative context-similarity approach from
reasoning: an exact context_id match is a strong (1.0) match; otherwise we
fall back to a conservative lower bound. This keeps decision-making a pure
function of (context, candidate) and provides the ``context_match`` factor
used in scoring (D3).
"""


def context_id_of(context):
    if isinstance(context, dict):
        return context.get("context_id")
    return getattr(context, "context_id", None) or ""


def context_match(context, other_context_id):
    """Deterministic context similarity score in [0, 1].

    ``other_context_id == current context_id`` -> 1.0; otherwise a
    conservative fallback. ``context`` may be a dict or a ContextSnapshot.
    """
    current = context_id_of(context)
    if not current:
        return 0.5
    if other_context_id == current:
        return 1.0
    return 0.5


def strategy_context_applicable(strategy, context):
    """Whether a strategy's context_restrictions admit ``context``.

    No restrictions -> applicable. ``allowed_contexts`` and ``system`` keys
    (mirroring the knowledge lifecycle shape) are honored.
    """
    restrictions = getattr(strategy, "context_restrictions", None)
    if not restrictions:
        return True
    if not isinstance(restrictions, dict):
        return True
    current = context_id_of(context)
    allowed = restrictions.get("allowed_contexts")
    if isinstance(allowed, (list, tuple)) and allowed:
        if current and current not in allowed:
            return False
    system = restrictions.get("system")
    if isinstance(system, str) and system and current and system != current:
        return False
    return True
