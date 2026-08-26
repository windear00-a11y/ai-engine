"""Static, deterministic verification of an acceptance import plan.

The verifier checks the ENTIRE plan before any (simulated) import is possible:

* valid node structure and canonical node types
* unique node identities (no duplicate primary keys)
* valid relationship structure and referential integrity:
    - the source node MUST exist (the repository enforces this FK)
    - a dangling TARGET is allowed (the repository intentionally does NOT make
      ``target_node_id`` a foreign key), so it is a counted warning, not an error
* provenance present on every node, with valid evidence references
* no malformed metadata
* no duplicate relationship primary keys (source, type, target)
* no unsafe filesystem paths (NUL bytes, absolute or ``..``-escaping paths in
  provenance locations)
* the acceptance read-only guard is present and enabled

Verification is purely static and read-only: it never creates or touches any
database, and never re-runs the acceptance layer.
"""

import os
from dataclasses import dataclass, field

from retrieval.knowledge import VALID_TYPES, RELATIONSHIP_KINDS
from retrieval.vocabulary import (
    is_valid_node_type,
    is_recommended_node_type,
    is_recommended_relationship_kind,
)


@dataclass
class Issue:
    """One deterministic finding (error or warning)."""
    code: str
    message: str
    path: str = ""

    def as_dict(self):
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass
class VerificationResult:
    """Outcome of statically verifying one import plan."""
    valid: bool
    errors: list = field(default_factory=list)      # Issue (fatal)
    warnings: list = field(default_factory=list)    # Issue (non-fatal)
    plan: dict = None
    nodes: list = field(default_factory=list)       # proposed node dicts
    relationships: list = field(default_factory=list)  # proposed rel dicts
    node_ids: set = field(default_factory=set)
    dangling_targets: list = field(default_factory=list)
    unresolved_sources: list = field(default_factory=list)

    def as_dict(self):
        return {
            "valid": self.valid,
            "errors": [e.as_dict() for e in self.errors],
            "warnings": [w.as_dict() for w in self.warnings],
            "node_count": len(self.nodes),
            "relationship_count": len(self.relationships),
        }


def _issue(code, message, path):
    return Issue(code, message, path)


def _is_unsafe_path(value):
    """Unsafe = contains NUL, is absolute, or escapes via ``..`` segments.

    Provenance locations are documentation-relative paths; an absolute path or
    a traversal would be an unsafe pointer. The plan's own top-level ``source``
    is a real on-disk path of the candidate output, so it is only checked for
    NUL bytes / type.
    """
    if not isinstance(value, str):
        return True
    if "\x00" in value:
        return True
    norm = value.replace("\\", "/")
    if norm.startswith("/"):
        return True
    if "://" in norm:  # scheme URLs are not filesystem paths
        return False
    for seg in norm.split("/"):
        if seg == "..":
            return True
    return False


def _check_node(node, index, errors, warnings, seen_ids):
    path = f"preview.proposed_nodes[{index}]"
    if not isinstance(node, dict):
        errors.append(_issue("malformed_node", "node must be an object", path))
        return

    nid = node.get("id")
    if not isinstance(nid, str) or not nid:
        errors.append(_issue("node_id",
                             "node 'id' is required and must be a non-empty string",
                             f"{path}.id"))
    else:
        if nid in seen_ids:
            errors.append(_issue("duplicate_node_id",
                                 f"duplicate node id {nid!r} "
                                 f"(first at proposed_nodes[{seen_ids[nid]}])",
                                 f"{path}.id"))
        else:
            seen_ids[nid] = index

    ntype = node.get("type")
    if not is_valid_node_type(ntype):
        errors.append(_issue("node_type",
                             f"node type must be a non-empty string, got {ntype!r}",
                             f"{path}.type"))
    elif not is_recommended_node_type(ntype):
        warnings.append(_issue("node_type",
                               f"node type {ntype!r} is not in the recommended "
                               f"vocabulary ({sorted(VALID_TYPES)})",
                               f"{path}.type"))

    name = node.get("name")
    if not isinstance(name, str) or not name:
        errors.append(_issue("node_name",
                             "node 'name' is required and must be a non-empty string",
                             f"{path}.name"))

    desc = node.get("description")
    if not isinstance(desc, str):
        errors.append(_issue("node_description",
                             "node 'description' must be a string",
                             f"{path}.description"))

    provenance = node.get("provenance")
    if not isinstance(provenance, list) or not provenance:
        errors.append(_issue("missing_provenance",
                             "node must carry a non-empty provenance list",
                             f"{path}.provenance"))
    else:
        _check_provenance(provenance, f"{path}.provenance", errors)


def _check_provenance(provenance, path, errors):
    for j, pv in enumerate(provenance):
        ppath = f"{path}[{j}]"
        if not isinstance(pv, dict):
            errors.append(_issue("malformed_metadata",
                                 "provenance entry must be an object", ppath))
            continue
        cid = pv.get("candidate_id")
        if not isinstance(cid, str) or not cid:
            errors.append(_issue("evidence_reference",
                                 "provenance candidate_id is required",
                                 f"{ppath}.candidate_id"))
        evidence = pv.get("evidence")
        if not isinstance(evidence, str) or not evidence:
            errors.append(_issue("evidence_reference",
                                 "provenance evidence is required and must be "
                                 "a non-empty string", f"{ppath}.evidence"))
        location = pv.get("location")
        if location is not None:
            if not isinstance(location, dict):
                errors.append(_issue("malformed_metadata",
                                     "provenance location must be an object",
                                     f"{ppath}.location"))
            else:
                loc_path = location.get("path")
                if not isinstance(loc_path, str) or not loc_path:
                    errors.append(_issue("malformed_metadata",
                                         "location.path is required",
                                         f"{ppath}.location.path"))
                elif _is_unsafe_path(loc_path):
                    errors.append(_issue("unsafe_path",
                                         f"unsafe provenance path {loc_path!r}",
                                         f"{ppath}.location.path"))
        if "\x00" in str(pv):
            errors.append(_issue("unsafe_path", "provenance entry contains a "
                                                "NUL byte", ppath))


def _check_relationship(rel, index, errors, warnings, node_ids,
                        dangling_targets, unresolved_sources, seen_rel):
    path = f"preview.proposed_relationships[{index}]"
    if not isinstance(rel, dict):
        errors.append(_issue("malformed_relationship",
                             "relationship must be an object", path))
        return

    src = rel.get("source_node_id")
    rtype = rel.get("relationship_type")
    tgt = rel.get("target_node_id")
    if not isinstance(src, str) or not src:
        errors.append(_issue("relationship_structure",
                             "relationship 'source_node_id' is required and "
                             "must be a non-empty string",
                             f"{path}.source_node_id"))
    if not isinstance(rtype, str) or not rtype:
        errors.append(_issue("relationship_structure",
                             "relationship 'relationship_type' is required and "
                             "must be a non-empty string",
                             f"{path}.relationship_type"))
    if not isinstance(tgt, str) or not tgt:
        errors.append(_issue("relationship_structure",
                             "relationship 'target_node_id' is required and "
                             "must be a non-empty string",
                             f"{path}.target_node_id"))

    label = rel.get("label")
    if label is not None and not isinstance(label, str):
        errors.append(_issue("malformed_metadata",
                             "relationship 'label' must be a string when provided",
                             f"{path}.label"))

    if isinstance(rtype, str) and rtype and not is_recommended_relationship_kind(rtype):
        warnings.append(_issue("relationship_kind",
                               f"relationship type {rtype!r} is not in the "
                               f"recommended vocabulary {sorted(RELATIONSHIP_KINDS)}",
                               f"{path}.relationship_type"))

    if isinstance(src, str) and src and src not in node_ids:
        unresolved_sources.append(rel)
        errors.append(_issue("relationship_source_missing",
                             f"relationship source node {src!r} is not defined "
                             f"in the plan (repository FK requires it)",
                             f"{path}.source_node_id"))

    if isinstance(tgt, str) and tgt and tgt not in node_ids:
        dangling_targets.append(rel)

    if isinstance(src, str) and src and isinstance(rtype, str) and rtype \
            and isinstance(tgt, str) and tgt:
        key = (src, rtype, tgt)
        if key in seen_rel:
            errors.append(_issue("duplicate_relationship",
                                 f"duplicate relationship {key!r}",
                                 path))
        else:
            seen_rel.add(key)


def verify_plan(plan):
    """Verify a parsed import plan; return a :class:`VerificationResult`.

    Pure and deterministic. Never touches a database and never writes
    anything: it only reads the plan in memory.
    """
    errors, warnings = [], []

    if not isinstance(plan, dict):
        return VerificationResult(False, [_issue("invalid_plan",
                                                 "import plan must be a JSON object",
                                                 "")])

    guard = plan.get("read_only_guard")
    if not isinstance(guard, dict) or guard.get("sqlite_writes_forbidden") is not True:
        errors.append(_issue("guard_off",
                             "plan read-only guard must be present and enabled "
                             "(sqlite_writes_forbidden=true)",
                             "read_only_guard"))

    src_field = plan.get("source")
    if src_field is not None and (not isinstance(src_field, str) or "\x00" in src_field):
        errors.append(_issue("unsafe_path", "plan 'source' must be a safe string",
                             "source"))

    preview = plan.get("preview")
    if not isinstance(preview, dict):
        errors.append(_issue("invalid_plan", "plan must contain a 'preview' object",
                             "preview"))
        return VerificationResult(len(errors) == 0, errors, warnings, plan)

    nodes = preview.get("proposed_nodes")
    rels = preview.get("proposed_relationships")
    if not isinstance(nodes, list):
        errors.append(_issue("invalid_plan",
                             "'preview.proposed_nodes' must be a list",
                             "preview.proposed_nodes"))
        nodes = []
    if not isinstance(rels, list):
        errors.append(_issue("invalid_plan",
                             "'preview.proposed_relationships' must be a list",
                             "preview.proposed_relationships"))
        rels = []

    expected = preview.get("proposed_nodes_count")
    if isinstance(expected, int) and expected != len(nodes):
        errors.append(_issue("count_mismatch",
                             f"proposed_nodes_count {expected} does not match "
                             f"actual node count {len(nodes)}",
                             "preview.proposed_nodes_count"))
    expected_rel = preview.get("proposed_relationships_count")
    if isinstance(expected_rel, int) and expected_rel != len(rels):
        errors.append(_issue("count_mismatch",
                             f"proposed_relationships_count {expected_rel} does "
                             f"not match actual relationship count {len(rels)}",
                             "preview.proposed_relationships_count"))

    seen_ids = {}
    for i, node in enumerate(nodes):
        _check_node(node, i, errors, warnings, seen_ids)
    node_ids = set(seen_ids)

    seen_rel = set()
    dangling_targets = []
    unresolved_sources = []
    for i, rel in enumerate(rels):
        _check_relationship(rel, i, errors, warnings, node_ids,
                            dangling_targets, unresolved_sources, seen_rel)

    if dangling_targets:
        warnings.append(_issue(
            "relationship_target_dangling",
            f"{len(dangling_targets)} relationship targets are dangling "
            f"(allowed by repository: target_node_id is not a foreign key)",
            "preview.proposed_relationships"))

    return VerificationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        plan=plan,
        nodes=list(nodes),
        relationships=list(rels),
        node_ids=node_ids,
        dangling_targets=dangling_targets,
        unresolved_sources=unresolved_sources,
    )
