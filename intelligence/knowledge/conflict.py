"""Conflict detection for knowledge (Phase 5).

Deterministic detection of contradictory knowledge entries, from observable
metadata and lifecycle signals. No domain-specific semantics are required;
detection is a pure function of the node set, so identical inputs always yield
identical conflicts.
"""

from .lifecycle import _get_lifecycle


class KnowledgeConflict:
    __slots__ = ("node_a_id", "node_b_id", "conflict_type", "severity",
                 "description")

    def __init__(self, node_a_id, node_b_id, conflict_type, severity,
                 description):
        self.node_a_id = node_a_id
        self.node_b_id = node_b_id
        self.conflict_type = conflict_type
        self.severity = severity
        self.description = description


def _active(node):
    status = _get_lifecycle(node).get("status", "active")
    return status in ("active", "pending_superseded", "pending_invalidated")


def _subject(node):
    if not isinstance(node, dict):
        return ""
    # KnowledgeRepository flattens stored metadata keys into the node dict, so
    # the subject surfaces as a top-level key.
    return (node.get("subject") or node.get("name") or "").strip()


def detect_conflicts(nodes, knowledge_ids=None):
    """Return a list of :class:`KnowledgeConflict`.

    ``nodes`` is an iterable of node dicts (as returned by
    KnowledgeRepository). If ``knowledge_ids`` is provided, only those ids are
    considered. Deterministic ordering by (severity desc, id).
    """
    by_id = {}
    for node in nodes:
        nid = node.get("id")
        if nid is None:
            continue
        by_id[nid] = node
    if knowledge_ids is not None:
        selected = {k for k in knowledge_ids if k in by_id}
    else:
        selected = set(by_id)

    conflicts = {}

    def add(a, b, ctype, severity, desc):
        key = tuple(sorted((a, b)))
        if key not in conflicts:
            conflicts[key] = KnowledgeConflict(key[0], key[1], ctype,
                                               severity, desc)

    nu = {}
    for nid in sorted(selected, key=lambda k: by_id[k].get("name", "")):
        node = by_id[nid]
        status = _get_lifecycle(node).get("status", "active")

        # mixed lifecycle: superseded and invalidated at once
        if "superseded_by" in _get_lifecycle(node) and status == "invalidated":
            add(nid, _get_lifecycle(node)["superseded_by"],
                "mixed_lifecycle", 0.9,
                f"node {nid} is both superseded and invalidated")

        # duplicate active subjects: two active nodes, same type + subject
        if _active(node) and _subject(node):
            key = (node.get("type"), _subject(node))
            if key in nu:
                other = nu[key]
                add(nid, other, "duplicate_subject", 0.6,
                    f"active nodes {other} and {nid} share subject "
                    f"{_subject(node)!r} (type {node.get('type')})")
            else:
                nu[key] = nid

        # supersession conflict: successor itself superseded
        succ = _get_lifecycle(node).get("superseded_by")
        if succ and succ in by_id:
            succ_status = _get_lifecycle(by_id[succ]).get("status", "active")
            if succ_status in ("superseded", "invalidated"):
                add(nid, succ, "supersession", 0.8,
                    f"node {nid} superseded by {succ}, which is itself "
                    f"{succ_status}")

    result = list(conflicts.values())
    result.sort(key=lambda c: (-c.severity,
                               c.node_a_id or "", c.node_b_id or ""))
    return result
