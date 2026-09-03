"""Evidence chain construction and confidence propagation (Phase 6).

Confidence propagation follows the Canonical Model (R2, R4):

* R2 -- conclusion confidence <= min(supporting evidence confidence)
        x chain_quality.
* R4 -- chains may chain multiple inference steps; depth is bounded to a
        maximum so inference cannot run away.

The chain quality is a deterministic, monotonic function of the number of
independent supports (more corroboration -> higher chain quality, capped at 1).

Everything here is a pure function; identical inputs always yield identical
chains and identical propagated confidence.
"""

from .types import EvidenceChain, EvidenceStep


def chain_quality(num_independent_supports):
    """Deterministic chain quality from the number of independent supports.

    1 support  -> 0.80
    2 supports -> 0.95
    3+ supports -> 1.00 (capped)
    More corroboration raises the quality of the chain.
    """
    n = max(1, int(num_independent_supports))
    return round(min(1.0, 0.80 + 0.15 * (n - 1)), 6)


def build_chain(steps):
    """Build an :class:`EvidenceChain` from an ordered list of steps.

    Steps must be :class:`EvidenceStep`. The chain is capped to
    ``EvidenceChain.MAX_DEPTH`` steps (R4). Confidence propagation:
        propagated = min(supporting confidence) * chain_quality
    where chain_quality grows with the number of independent supports.
    """
    capped = list(steps)[:EvidenceChain.MAX_DEPTH]
    if not capped:
        capped = [EvidenceStep("", "context", "no support", 0.0,
                               "insufficient support")]
    distinct_supports = {s.source_id for s in capped if s.source_id}
    n_supports = len(distinct_supports) if distinct_supports else 1
    min_conf = min(s.confidence for s in capped)
    quality = chain_quality(n_supports)
    propagated = round(min(1.0, min_conf * quality), 6)
    return EvidenceChain(steps=capped, chain_quality=quality,
                         propagated_confidence=propagated, depth=len(capped))
