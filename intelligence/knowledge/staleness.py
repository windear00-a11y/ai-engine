"""Staleness detection for knowledge (Phase 5).

Deterministic identification of outdated knowledge. A knowledge node is stale
if it has been superseded or invalidated, if its confidence is low, or if its
context restrictions exclude the context of interest. Pure function of node
state, so results are reproducible.
"""

from .lifecycle import _get_lifecycle

_CONFIDENCE_STALE_THRESHOLD = 0.3


class StalenessRecord:
    __slots__ = ("knowledge_id", "staleness_score", "reasons",
                 "observed_at_epoch")

    def __init__(self, knowledge_id, staleness_score, reasons,
                 observed_at_epoch=0.0):
        self.knowledge_id = knowledge_id
        self.staleness_score = round(float(staleness_score), 6)
        self.reasons = list(reasons)
        self.observed_at_epoch = float(observed_at_epoch)


def _confidence(node):
    lifecycle = _get_lifecycle(node)
    try:
        value = float(lifecycle.get("confidence", 0.0))
    except (TypeError, ValueError):
        value = 0.0
    return max(0.0, min(1.0, value))


def _context_excludes(node, context_id):
    if not context_id:
        return False
    lifecycle = _get_lifecycle(node)
    restrictions = lifecycle.get("context_restrictions") or {}
    if not isinstance(restrictions, dict):
        return False
    allowed = restrictions.get("allowed_contexts")
    if isinstance(allowed, (list, tuple)) and allowed:
        return context_id not in allowed
    system = restrictions.get("system")
    if isinstance(system, str) and system and system != context_id:
        return True
    return False


def staleness_score(node, context_id=None):
    """Deterministic staleness score in [0, 1] for a single node."""
    lifecycle = _get_lifecycle(node)
    status = lifecycle.get("status", "active")
    if status == "superseded":
        return 1.0, ["superseded"]
    if status == "invalidated":
        return 1.0, ["invalidated"]
    if status == "pending_superseded":
        return 0.85, ["pending supersession"]
    if status == "pending_invalidated":
        return 0.85, ["pending invalidation"]

    reasons = []
    conf = _confidence(node)
    if conf < _CONFIDENCE_STALE_THRESHOLD and conf > 0.0:
        reasons.append(f"low confidence ({conf:.2f})")
    if _context_excludes(node, context_id):
        reasons.append("context restriction excludes current context")

    if not reasons:
        return 0.0, []
    # Weighted: low confidence dominates, context mismatch moderate
    base = 0.5 if reasons else 0.0
    return base + 0.1 * (len(reasons) - 1), reasons


def detect_staleness(nodes, context_id=None, observed_at_epoch=0.0):
    """Return a list of :class:`StalenessRecord` for stale knowledge."""
    records = []
    for node in nodes:
        nid = node.get("id")
        if nid is None:
            continue
        score, reasons = staleness_score(node, context_id=context_id)
        if score > 0.0:
            records.append(StalenessRecord(
                nid, score, reasons, observed_at_epoch=observed_at_epoch))
    records.sort(key=lambda r: (-r.staleness_score, r.knowledge_id or ""))
    return records
