"""Contradiction detection (Phase 6, R3).

The reasoning engine detects three kinds of contradiction and reports them
without auto-resolving:

* knowledge   -- two knowledge claims about the same subject oppose each other
                 (K1 says X, K2 says not-X).
* experience  -- a strategy/task_type both succeeded and failed in comparable
                 contexts.
* context     -- a strategy works in one context but fails in a comparable one.

Cleanly deterministic: identical node/experience sets yield identical
contradictions.
"""

from .types import Contradiction

# Substrings that mark a claim as negating another (heuristic, deterministic).
_NEGATIONS = ("not ", "never ", "avoid ", "do not ", "don't ")


def _claim(node):
    """Extract a comparable claim string from a knowledge node (flattened)."""
    if not isinstance(node, dict):
        return ""
    return (node.get("claim") or node.get("name")
            or node.get("description") or "").strip()


def _subject(node):
    return (node.get("subject") or node.get("name") or "").strip()


def detect_knowledge_contradictions(nodes):
    """Detect direct contradictions among active knowledge nodes."""
    results = []
    active = [n for n in nodes
              if (n or {}).get("lifecycle", {}).get("status", "active")
              in ("active", "pending_superseded", "pending_invalidated")]
    # Group by subject; within a group, flag opposite-polarity claims.
    groups = {}
    for node in active:
        subj = _subject(node)
        if subj:
            groups.setdefault(subj, []).append(node)
    for subj, members in sorted(groups.items()):
        members = sorted(members, key=lambda n: n.get("id", ""))
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = members[i], members[j]
                ca, cb = _claim(a).lower(), _claim(b).lower()
                polarization = False
                if ca and cb:
                    if any(ca.startswith(neg) for neg in _NEGATIONS) != \
                       any(cb.startswith(neg) for neg in _NEGATIONS):
                        polarization = True
                if polarization:
                    results.append(Contradiction(
                        "knowledge", a.get("id"), b.get("id"), 0.9,
                        f"knowledge nodes {a.get('id')} and {b.get('id')} "
                        f"make opposing claims about '{subj}'"))
    results.sort(key=lambda c: (c.node_a or "", c.node_b or ""))
    return results


def detect_experience_contradictions(experiences):
    """Detect strategies that both succeeded and failed comparably."""
    from collections import defaultdict
    groups = defaultdict(list)
    for exp in experiences:
        task_type = getattr(exp, "task_type", None)
        if task_type:
            groups[task_type].append(exp)

    def _ok(exp):
        summary = getattr(exp, "summary", None) or {}
        return str(summary.get("outcome", "")).lower() in (
            "success", "succeeded", "success_verified", "ok", "pass")

    results = []
    for task_type, records in sorted(groups.items()):
        successes = [r for r in records if _ok(r)]
        failures = [r for r in records if not _ok(r)]
        if successes and failures:
            results.append(Contradiction(
                "experience", task_type, task_type, 0.7,
                f"task_type '{task_type}' both succeeded "
                f"({len(successes)}x) and failed ({len(failures)}x)"))
    results.sort(key=lambda c: c.node_a or "")
    return results


def detect_context_contradictions(knowledge_nodes):
    """Detect strategies that work in one context but fail in another.

    Uses lifecycle context_restrictions on otherwise-similar knowledge.
    """
    results = []
    seen = set()
    for node in knowledge_nodes:
        if not isinstance(node, dict):
            continue
        nid = node.get("id")
        if not nid or nid in seen:
            continue
        seen.add(nid)
        restrictions = (node.get("lifecycle") or {}).get(
            "context_restrictions") or {}
        allowed = restrictions.get("allowed_contexts")
        if isinstance(allowed, (list, tuple)) and len(allowed) >= 2:
            results.append(Contradiction(
                "context", str(allowed[0]), str(allowed[1]), 0.5,
                f"node {nid} is restricted to multiple distinct "
                f"contexts ({allowed})"))
    results.sort(key=lambda c: (c.node_a or "", c.node_b or ""))
    return results


def detect_all_contradictions(nodes, experiences):
    """Run all contradiction detectors and merge, deterministically ordered."""
    results = (detect_knowledge_contradictions(nodes)
               + detect_experience_contradictions(experiences)
               + detect_context_contradictions(nodes))
    results.sort(key=lambda c: (-c.severity, c.contradiction_type,
                                c.node_a or "", c.node_b or ""))
    return results
