"""Deterministic provenance tracing over lifecycle records (Phase 26).

``trace_provenance`` walks the derived-from / parent / evidence / outcome
edges linking STRATEGY -> LEARNING -> EXPERIENCE -> OUTCOME -> EVIDENCE ->
SOURCE (and context), producing a stable, cycle-safe, depth-bounded trace.

The module is PURE: it has no idea what stores back the records. It receives a
duck-typed ``accessor(record_id, role)`` returning a normalized record dict::

    {
        "record_id": str,
        "role": str,
        "subject": str | None,
        "origin": str,
        "lifecycle_state": str,
        "content": dict,
        "derived_from": list[str],
        "parent_record_ids": list[str],
        "evidence_ids": list[str],
        "outcome_id": str | None,
        "context_id": str | None,
        "experience_ids": list[str],   # strategy -> supporting experiences
        "source_observation_id": str | None,  # evidence -> source observation
    }

Anything returned by the accessor must be deterministic for the same
(record_id, role): the trace result is then inherently deterministic.
"""

import json
from collections import deque
from typing import Callable, Optional


def _norm(entry):
    if entry is None:
        return None
    if isinstance(entry, str):
        return None
    return entry


def normalize_entry(record) -> dict:
    """Normalize any record-shaped object into the accessor contract."""
    if hasattr(record, "as_dict"):
        record = record.as_dict()
    if not isinstance(record, dict):
        return {}
    content = record.get("content") or {}
    provenance = record.get("provenance") or {}
    derived_from = list(provenance.get("derived_from")
                        or record.get("derived_from") or ())
    parent_ids = list(provenance.get("parent_record_ids")
                      or record.get("parent_record_ids") or ())
    evidence_ids = list(provenance.get("evidence_ids")
                        or record.get("evidence_ids") or ())
    outcome_id = content.get("outcome_id") or record.get("outcome_id")
    context_id = (provenance.get("context_id") or content.get("context_id")
                  or record.get("context_id"))
    return {
        "record_id": record.get("record_id") or provenance.get("record_id"),
        "role": record.get("role") or provenance.get("role"),
        "subject": record.get("subject"),
        "origin": (provenance.get("origin") or record.get("origin")
                   or _role_default_origin(record.get("role"))),
        "lifecycle_state": (provenance.get("lifecycle_state")
                            or record.get("lifecycle_state") or "active"),
        "content": content,
        "derived_from": derived_from,
        "parent_record_ids": parent_ids,
        "evidence_ids": list(provenance.get("evidence_ids")
                             or record.get("evidence_ids")
                             or content.get("evidence_ids") or ()),
        "outcome_id": outcome_id,
        "context_id": context_id,
        "reasoning_id": content.get("reasoning_id")
        or record.get("reasoning_id"),
        "decision_id": content.get("decision_id")
        or record.get("decision_id"),
        "strategy_application_id": content.get("strategy_application_id")
        or record.get("strategy_application_id"),
        "applicable_strategy_id": content.get("applicable_strategy_id")
        or record.get("applicable_strategy_id"),
        "plan_id": content.get("plan_id") or record.get("plan_id"),
        "plan_step_id": (content.get("plan_step_id")
                         or record.get("plan_step_id")),
        "authority_id": content.get("authority_id")
        or record.get("authority_id"),
        "action_id": content.get("action_id") or record.get("action_id"),
        "verification_id": (content.get("verification_id")
                            or record.get("verification_id")),
        "observation_ids": list(content.get("observation_ids")
                                or content.get("observation_references")
                                or record.get("observation_ids") or ()),
        "expectations": (content.get("expectations")
                         or content.get("expected_conditions") or {}),
        "experience_ids": list(content.get("experience_ids")
                               or content.get("supporting_experience_ids")
                               or record.get("experience_ids") or ()),
        "knowledge_ids": list(content.get("knowledge_ids") or ()),
        "strategy_ids": list(content.get("applicable_strategy_ids")
                             or content.get("strategy_ids") or ()),
        "source_observation_id": (content.get("source_observation_id")
                                  or record.get("source_observation_id")),
    }


def _role_default_origin(role):
    defaults = {
        "strategy": "derived",
        "strategy_application": "derived",
        "reasoning": "derived",
        "decision": "derived",
        "plan": "derived",
        "authority": "derived",
        "authorization": "user_provided",
        "action": "observed",
        "observation": "observed",
        "verification": "derived",
        "learning": "derived",
        "experience": "observed",
        "outcome": "observed",
        "evidence": "observed",
        "context": "system_defined",
        "source_record": "system_defined",
        "knowledge": "user_provided",
        "memory": "user_provided",
    }
    return defaults.get(role, "user_provided")


def derive_child_links(entry, accessor) -> list:
    """Deterministically yield the child edges of a normalized record.

    Child edges returned as ``(relation, role, record_id)``:
      * STRATEGY -> LEARNING  (:ref "derived_from")
      * STRATEGY -> EXPERIENCE (:ref "from_experience")
      * STRATEGY -> EVIDENCE   (:ref "supported_by")
      * LEARNING -> EXPERIENCE (:ref "from_experience")
      * EXPERIENCE -> OUTCOME  (:ref "led_to_outcome")
      * EXPERIENCE -> EVIDENCE (:ref "supported_by")
      * EXPERIENCE -> CONTEXT  (:ref "in_context")
      * OUTCOME -> EVIDENCE    (:ref "verified_by")
      * EVIDENCE -> SOURCE     (:ref "recorded_from")
      * MEMORY/KNOWLEDGE/SOURCE_RECORD -> EVIDENCE / CONTEXT / PARENT

    Edges are deduplicated and sorted, and the child role is derived from the
    relation so the accessor can resolve it deterministically.
    """
    role = entry.get("role")
    links = []

    def add(relation, child_role, child_id):
        if child_id is None:
            return
        if child_id == entry.get("record_id") and child_role == role:
            return
        links.append((relation, child_role, child_id))

    if not role:
        return links

    if role == "strategy":
        for cid in sorted(entry.get("derived_from") or ()):
            add("derived_from", "learning", cid)
        for cid in sorted(entry.get("experience_ids") or ()):
            add("from_experience", "experience", cid)
        for cid in sorted(entry.get("evidence_ids") or ()):
            add("supported_by", "evidence", cid)
    elif role == "learning":
        for cid in sorted(set(entry.get("derived_from") or ())
                          | set(entry.get("parent_record_ids") or ())):
            add("from_experience", "experience", cid)
    elif role == "experience":
        add("led_to_outcome", "outcome", entry.get("outcome_id"))
        for cid in sorted(entry.get("evidence_ids") or ()):
            add("supported_by", "evidence", cid)
        add("in_context", "context", entry.get("context_id"))
        for cid in sorted(entry.get("parent_record_ids") or ()):
            add("from_record", "source_record", cid)
    elif role == "outcome":
        add("based_on_verification", "verification", entry.get("verification_id"))
        for cid in sorted(entry.get("evidence_ids") or ()):
            add("verified_by", "evidence", cid)
        for cid in sorted(entry.get("parent_record_ids") or ()):
            add("from_record", "source_record", cid)
    elif role == "evidence":
        add("recorded_from", "source_record", entry.get("source_observation_id"))
        for cid in sorted(entry.get("parent_record_ids") or ()):
            add("from_record", "source_record", cid)
    elif role in ("memory", "knowledge", "source_record", "context"):
        for cid in sorted(entry.get("evidence_ids") or ()):
            add("supported_by", "evidence", cid)
        add("in_context", "context", entry.get("context_id"))
        for cid in sorted(entry.get("parent_record_ids") or ()):
            add("from_record", "source_record", cid)

    # Phase 28 derived chain: strategy_application -> strategy;
    # reasoning -> strategy_application / strategy / evidence / experience /
    # knowledge / context; decision -> reasoning / strategy_application /
    # strategy; plan -> decision.
    elif role == "strategy_application":
        for cid in sorted(set(entry.get("derived_from") or ())):
            add("derived_from", "strategy", cid)
        for cid in sorted(entry.get("experience_ids") or ()):
            add("from_experience", "experience", cid)
        for cid in sorted(entry.get("evidence_ids") or ()):
            add("supported_by", "evidence", cid)
        add("in_context", "context", entry.get("context_id"))
    elif role == "reasoning":
        for cid in sorted(entry.get("derived_from") or ()):
            add("based_on_application", "strategy_application", cid)
        for cid in sorted(entry.get("strategy_ids") or ()):
            add("applies_strategy", "strategy", cid)
        for cid in sorted(entry.get("evidence_ids") or ()):
            add("supported_by", "evidence", cid)
        for cid in sorted(entry.get("experience_ids") or ()):
            add("from_experience", "experience", cid)
        for cid in sorted(entry.get("knowledge_ids") or ()):
            add("informed_by", "knowledge", cid)
        add("in_context", "context", entry.get("context_id"))
    elif role == "decision":
        for cid in sorted(entry.get("derived_from") or ()):
            add("derived_from", "reasoning", cid)
        for cid in sorted(entry.get("parent_record_ids") or ()):
            add("from_application", "strategy_application", cid)
        add("applied_strategy", "strategy", entry.get("applicable_strategy_id"))
        add("applied_strategy", "strategy_application",
            entry.get("strategy_application_id"))
    elif role == "plan":
        for cid in sorted(set(entry.get("derived_from") or ())):
            add("derived_from", "decision", cid)
        add("based_on_decision", "decision", entry.get("decision_id"))

    # Phase 29 execution chain: plan -> authority/approval -> action ->
    # observation -> verification -> outcome -> experience.
    elif role == "authority":
        for cid in sorted(set(entry.get("derived_from") or ())
                          | set(entry.get("parent_record_ids") or ())):
            add("based_on_plan", "plan", cid)
    elif role == "authorization":
        for cid in sorted(entry.get("parent_record_ids") or ()):
            add("grants_plan", "plan", cid)
    elif role == "action":
        add("based_on_plan", "plan", entry.get("plan_id"))
        add("approved_by", "authority", entry.get("authority_id"))
        for cid in sorted(entry.get("evidence_ids") or ()):
            add("supported_by", "evidence", cid)
    elif role == "observation":
        add("of_action", "action", entry.get("action_id"))
        for cid in sorted(entry.get("evidence_ids") or ()):
            add("supported_by", "evidence", cid)
    elif role == "verification":
        add("of_action", "action", entry.get("action_id"))
        for cid in sorted(entry.get("observation_ids") or ()):
            add("from_observation", "observation", cid)
        for cid in sorted(entry.get("evidence_ids") or ()):
            add("supported_by", "evidence", cid)

    # Dedupe (relation, role, id) while preserving the deterministic order.
    seen = set()
    ordered = []
    for triple in links:
        key = (triple[0], triple[1], triple[2])
        if key not in seen:
            seen.add(key)
            ordered.append(triple)
    ordered.sort(key=lambda t: (t[0], t[1], t[2]))
    return ordered


def trace_provenance(record_id: str, role: Optional[str],
                     accessor: Callable, max_depth: int = 16) -> dict:
    """Walk provenance edges from a root record, deterministically.

    Returns::

        {
          "root": normalized root entry,
          "records": [ordered normalized entries reachable from the root, incl. root],
          "meta": {"node_count": n, "max_depth": reached, "cycle_safe": True}
        }

    Missing root (accessor returns None) raises KeyError(record_id).
    """
    root = accessor(record_id, role)
    root = normalize_entry(root) if root else None
    if not root or not root.get("record_id"):
        raise KeyError(record_id)

    visited = set()
    queue = deque()
    root_rec = dict(root)
    root_rec["depth"] = 0
    visited.add((root_rec.get("role") or "", root_rec["record_id"]))
    queue.append(root_rec)
    records = []
    while queue:
        entry = queue.popleft()
        depth = entry.get("depth", 0)
        children = derive_child_links(entry, accessor)
        resolved_links = []
        for relation, child_role, child_id in children:
            child = accessor(child_id, child_role)
            resolved = False
            if isinstance(child, dict) and child.get("record_id"):
                child_norm = normalize_entry(child)
                key = (child_norm.get("role") or "", child_norm["record_id"])
                resolved = True
                resolved_links.append({
                    "relation": relation,
                    "role": child_role,
                    "record_id": child_id,
                    "resolved": True,
                })
                if key not in visited and depth + 1 <= max_depth:
                    visited.add(key)
                    child_entry = dict(child_norm)
                    child_entry["depth"] = depth + 1
                    queue.append(child_entry)
            else:
                resolved_links.append({
                    "relation": relation,
                    "role": child_role,
                    "record_id": child_id,
                    "resolved": False,
                })
        resolved_links.sort(key=lambda l: (l["relation"], l["role"], l["record_id"]))
        node = {
            "record_id": entry["record_id"],
            "role": entry.get("role"),
            "subject": entry.get("subject"),
            "origin": entry.get("origin"),
            "lifecycle_state": entry.get("lifecycle_state"),
            "depth": depth,
            "links": resolved_links,
        }
        records.append(node)

    return {
        "root": records[0] if records else None,
        "records": records,
        "meta": {
            "node_count": len(records),
            "max_depth": max(int(r.get("depth", 0)) for r in records) if records else 0,
            "cycle_safe": True,
        },
    }


def to_json(trace: dict) -> str:
    """JSON-serialize a trace result deterministically."""
    return json.dumps(trace, sort_keys=True, separators=(",", ":"),
                      default=str)