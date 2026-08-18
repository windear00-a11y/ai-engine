"""Deterministic acceptance reports and import preview.

``build_summary`` aggregates the evaluation outcome (decisions, reasons,
identities, provenance). ``build_preview`` additionally maps every ACCEPTED
identity to proposed Knowledge Schema nodes / relationships -- an in-memory
preview that NEVER touches SQLite.
"""

import hashlib
import json
from collections import Counter, defaultdict

from acceptance.types import ACCEPT, HOLD, REJECT, normalize_code
from acceptance import mapping
from knowledge_compiler.types import CAND_CODE_EXAMPLE


def _identity_summary(group):
    docs = group.documents
    return {
        "identity": group.identity,
        "kind": group.kind,
        "canonical_candidate_id": group.canonical_candidate_id,
        "member_count": len(group.members),
        "document_count": len(docs),
        "documents": docs,
        "conflict": group.conflict,
    }


def _provenance_coverage(evaluation):
    """Provenance statistics over accepted identities."""
    accepted = evaluation.accepted_groups()
    by_doc_count = Counter(len(g.documents) for g in accepted)
    provenance_total = 0
    documents = set()
    for g in accepted:
        for m in g.members:
            provenance_total += 1
            if m.get("document"):
                documents.add(m["document"])
    return {
        "accepted_identities": len(accepted),
        "identity_document_count_distribution": {
            str(k): int(v) for k, v in sorted(by_doc_count.items())},
        "cross_document_identities": sum(
            1 for g in accepted if len(g.documents) > 1),
        "single_document_identities": sum(
            1 for g in accepted if len(g.documents) == 1),
        "distinct_documents_with_accepted_knowledge": len(sorted(documents)),
        "evidence_references_total": provenance_total,
    }


def to_json(payload):
    """Deterministic JSON serialization (stable key order)."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def build_summary(evaluation):
    """Aggregate decision/identity/reason statistics for an evaluation."""
    total = len(evaluation.decisions)
    counts = Counter(d.decision for d in evaluation.decisions)

    by_kind = defaultdict(Counter)
    by_reason = Counter()
    for d in evaluation.decisions:
        by_kind[d.kind][d.decision] += 1
        by_reason[d.reason_code] += 1

    groups = evaluation.groups
    duplicated = [g for g in groups if len(g.members) > 1]
    conflicts = [g for g in groups if g.conflict]
    cross_doc = [g for g in groups if len(g.documents) > 1]

    return {
        "total_candidates": total,
        "accepted": int(counts[ACCEPT]),
        "held": int(counts[HOLD]),
        "rejected": int(counts[REJECT]),
        "counts_by_kind": {
            kind: {
                "total": int(c[ACCEPT] + c[HOLD] + c[REJECT]),
                "accepted": int(c[ACCEPT]),
                "held": int(c[HOLD]),
                "rejected": int(c[REJECT]),
            }
            for kind, c in sorted(by_kind.items())
        },
        "counts_by_reason": dict(sorted(by_reason.items())),
        "duplicate_identities": {
            "identities_total": len(groups),
            "identities_with_multiple_members": len(duplicated),
            "cross_document_identities": len(cross_doc),
            "conflicted_identities": len(conflicts),
            "candidates_collapsed_by_identity": sum(
                len(g.members) - 1 for g in duplicated),
        },
        "provenance_coverage": _provenance_coverage(evaluation),
    }


def build_preview(evaluation):
    """Build the import preview: summary + proposed nodes/relationships.

    Only ACCEPTED, conflict-free, schema-mappable identities become proposed
    database nodes. This builds plain dicts in memory and does NOT write to
    SQLite or the Knowledge Repository.
    """
    summary = build_summary(evaluation)

    nodes = []
    node_types = Counter()
    relationships = []
    rel_types = Counter()
    proposed_node_ids = set()

    for grp in evaluation.accepted_groups():
        canonical = evaluation.canonical_member(grp)
        node = mapping.proposed_node(
            grp.identity, grp.kind, canonical.get("meta") or {},
            canonical.get("summary") or "",
            canonical.get("evidence") or "")
        rels = mapping.proposed_relationships(
            grp.identity, grp.kind, canonical.get("meta") or {},
            resolved=grp.resolved_endpoints)
        if node is not None:
            node["provenance"] = [
                {
                    "document": m.get("document") or "",
                    "section_path": list(m.get("section_path") or []),
                    "location": dict(m.get("location") or {}),
                    "candidate_id": m.get("candidate_id") or "",
                    "evidence": m.get("evidence") or "",
                }
                for m in grp.members
            ]
            node_types[node["type"]] += 1
            nodes.append(node)
            proposed_node_ids.add(node["id"])
        for rel in rels:
            rel_types[rel["relationship_type"]] += 1
            relationships.append({
                "source_node_id": rel["source_node_id"],
                "relationship_type": rel["relationship_type"],
                "target_node_id": rel["target_node_id"],
                "label": rel["label"],
                "target_name": rel.get("target_name"),
                "provenance_identity": grp.identity,
            })

    nodes.sort(key=lambda n: n["id"])
    relationships.sort(key=lambda r: (r["source_node_id"],
                                      r["relationship_type"],
                                      r["target_node_id"]))

    return {
        "summary": summary,
        "proposed_nodes_count": len(nodes),
        "proposed_nodes_by_type": dict(sorted(node_types.items())),
        "proposed_relationships_count": len(relationships),
        "proposed_relationships_by_type": dict(sorted(rel_types.items())),
        "proposed_nodes": nodes,
        "proposed_relationships": relationships,
        "reference_integrity": {
            "dangling_relationship_targets": sum(
                1 for r in relationships
                if r["target_node_id"] not in proposed_node_ids),
        },
    }


def _example_category(evidence):
    """Classify an accepted code example for the audit (informational only)."""
    lines = [ln.strip() for ln in (evidence or "").splitlines() if ln.strip()]
    if not lines:
        return "empty"
    text = "\n".join(lines)
    if "Traceback (most recent call last):" in text:
        return "traceback_demo"
    if any(ln.startswith("raise ") for ln in lines):
        return "raises_demo"
    if all(ln.startswith("#") for ln in lines):
        return "comment_only"
    if len(lines) == 1:
        return "single_line"
    return "genuine"


def build_audit(evaluation):
    """Mapping audit: how ACCEPTED knowledge maps to canonical node types.

    Informational only -- it never changes decisions. For every ACCEPTED,
    conflict-free identity it reports the proposed node type; it also breaks
    down accepted code examples by category/language and the full decision
    stream by reason code, so a reviewer can verify every acceptance maps
    deterministically and spot accepted candidates that look out of place.
    """
    by_type = defaultdict(lambda: {"identities": 0, "members": 0})
    by_relationship = defaultdict(int)
    unmappable = []
    example_categories = Counter()
    example_languages = Counter()
    for grp in evaluation.accepted_groups():
        canonical = evaluation.canonical_member(grp)
        meta = canonical.get("meta") or {}
        if grp.kind == CAND_CODE_EXAMPLE:
            lang = (meta.get("language") or "none").lower()
            example_languages[lang] += len(grp.members)
            example_categories[_example_category(canonical.get("evidence"))] += 1
        ntype = mapping.node_type_for(grp.kind, meta)
        if ntype is None:
            rel = mapping.relationship_for(grp.kind)
            if rel is not None:
                by_relationship[rel[0]] += 1
            else:
                unmappable.append(grp.identity)
            continue
        by_type[ntype]["identities"] += 1
        by_type[ntype]["members"] += len(grp.members)

    by_reason = Counter(d.reason_code for d in evaluation.decisions)

    return {
        "accepted_identities_by_node_type": {
            t: {"accepted_identities": v["identities"],
                "candidate_members": v["members"]}
            for t, v in sorted(by_type.items())
        },
        "accepted_identities_relationship_only": {
            t: {"accepted_identities": v}
            for t, v in sorted(by_relationship.items())
        },
        "unmappable_accepted_identities": sorted(unmappable),
        "accepted_examples_by_language": dict(sorted(example_languages.items())),
        "accepted_examples_by_category": dict(sorted(example_categories.items())),
        "decisions_by_reason_code": dict(sorted(by_reason.items())),
    }