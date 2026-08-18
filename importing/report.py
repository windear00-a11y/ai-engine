"""Machine-readable and human-readable reports for import verification.

``build_report`` produces the stable report shape:

    {
      "safe": bool,
      "sources": [...],
      "nodes": {...},
      "relationships": {...},
      "conflicts": {...},
      "errors": [...],
      "warnings": [...],
      "provenance_coverage": {...},
      "projected_counts": {...},
      ... (additional informational keys)
    }

``human_summary`` renders the same facts as a compact text block for a CLI
reader. Everything is derived deterministically -- no timestamps, random
values, or environment-dependent ordering.
"""

import json
from collections import Counter


def to_json(payload):
    """Deterministic JSON serialization (stable key order)."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _plan_node_counts(verification):
    node_types = Counter(n.get("type") for n in verification.nodes)
    return dict(node_types)


def _plan_rel_counts(verification):
    rel_types = Counter(r.get("relationship_type")
                        for r in verification.relationships)
    return dict(rel_types)


def _identity_conflicts_from_plan(plan):
    decisions = (plan or {}).get("decisions")
    if not isinstance(decisions, list):
        return 0
    return sum(1 for d in decisions
               if d.get("decision") == "HOLD"
               and str(d.get("reason_code", "")).startswith("HOLD:identity"))


def _conflicts(verification, dryrun=None):
    conflicted = 0
    if dryrun is not None:
        conflicted = dryrun.conflicts.get("identity_conflicted_records", 0)
    else:
        conflicted = _identity_conflicts_from_plan(verification.plan)
    dup_nodes = sum(1 for e in verification.errors
                    if e.code == "duplicate_node_id")
    dup_rels = sum(1 for e in verification.errors
                   if e.code == "duplicate_relationship")
    return {
        "identity_conflicted_records": conflicted,
        "duplicate_node_ids": dup_nodes,
        "duplicate_relationships": dup_rels,
    }


def _provenance_coverage(verification, dryrun=None):
    if dryrun is not None:
        # The dry run's ground truth is what the isolated repository actually
        # holds after (simulated) apply -- zero on a rolled-back failure.
        total = dryrun.nodes_inserted
        checked = dryrun.provenance_verification.get("checked", 0)
        complete = dryrun.provenance_verification.get("complete", False)
    else:
        total = len(verification.nodes)
        checked = sum(1 for n in verification.nodes
                      if isinstance(n.get("provenance"), list)
                      and n["provenance"])
        complete = checked == total and total > 0
    coverage = round(checked / total, 6) if total else 0.0
    return {
        "nodes_with_provenance": checked,
        "total_nodes": total,
        "coverage": coverage,
        "complete": complete,
    }


def build_report(verification, dryrun=None):
    """Build the report. ``dryrun`` may be None for a static ``verify`` report."""
    if dryrun is None:
        nodes_count = len(verification.nodes)
        rels_count = len(verification.relationships)
        node_types = _plan_node_counts(verification)
        rel_types = _plan_rel_counts(verification)
        safe = verification.valid
        sources_inserted = 1 if verification.valid else 0
    else:
        nodes_count = dryrun.nodes_inserted
        rels_count = dryrun.relationships_inserted
        node_types = dryrun.node_types
        rel_types = dryrun.relationship_types
        safe = dryrun.safe
        sources_inserted = dryrun.sources_inserted

    source_name = ""
    source_version = None
    source_location = ""
    if dryrun is not None:
        source_name = dryrun.source.get("name", "")
        source_version = dryrun.source.get("version")
        source_location = dryrun.source.get("location", "")
    else:
        source_name, source_version, source_location = _source_identity(verification)

    return {
        "safe": safe,
        "sources": [{
            "name": source_name,
            "version": source_version,
            "location": source_location,
            "node_count": nodes_count,
            "relationship_count": rels_count,
        }],
        "nodes": {
            "count": nodes_count,
            "by_type": dict(sorted(node_types.items())),
            "with_provenance": _provenance_coverage(verification, dryrun)[
                "nodes_with_provenance"],
        },
        "relationships": {
            "count": rels_count,
            "by_type": dict(sorted(rel_types.items())),
            "dangling_targets": len(verification.dangling_targets),
            "skipped": len(verification.unresolved_sources),
        },
        "conflicts": _conflicts(verification, dryrun),
        "errors": (
            dryrun.errors if dryrun is not None else
            [e.as_dict() if hasattr(e, "as_dict") else e
             for e in verification.errors]
        ),
        "warnings": [w.as_dict() if hasattr(w, "as_dict") else w
                     for w in verification.warnings],
        "provenance_coverage": _provenance_coverage(verification, dryrun),
        "projected_counts": {
            "sources": sources_inserted,
            "nodes": nodes_count,
            "nodes_by_type": dict(sorted(node_types.items())),
            "relationships": rels_count,
            "relationships_by_type": dict(sorted(rel_types.items())),
        },
        "rollback": dryrun.rollback if dryrun is not None else None,
        "verified": verification.valid,
        "simulated": dryrun is not None,
        "skipped_held_records": (
            dryrun.skipped_held if dryrun is not None else None),
    }


def _source_identity(verification):
    plan = verification.plan or {}
    raw = plan.get("source") or ""
    base = raw.rstrip("/\\").rsplit("/", 1)[-1] if raw else "import-plan"
    version = plan.get("version")
    if version is not None and not isinstance(version, str):
        version = str(version)
    return (f"import-plan:{base or 'import-plan'}", version, raw or "<unknown>")


def human_summary(report):
    """Render the report as a compact human-readable block."""
    lines = []
    lines.append("Import plan verification")
    lines.append("=======================")
    safe = report.get("safe")
    lines.append(f"SAFE: {'YES' if safe else 'NO'}")
    lines.append("")

    if report.get("errors"):
        lines.append(f"Errors: {len(report['errors'])}")
        for e in report["errors"][:10]:
            lines.append(f"  - [{e.get('code')}] {e.get('message')}")
        if len(report["errors"]) > 10:
            lines.append(f"  ... and {len(report['errors']) - 10} more")
        lines.append("")

    if report.get("warnings"):
        lines.append(f"Warnings: {len(report['warnings'])}")
        for w in report["warnings"][:10]:
            lines.append(f"  - [{w.get('code')}] {w.get('message')}")
        lines.append("")

    for src in report.get("sources", []):
        lines.append("Source:")
        lines.append(f"  name        : {src.get('name')}")
        lines.append(f"  location    : {src.get('location')}")
        lines.append(f"  nodes       : {src.get('node_count')}")
        lines.append(f"  relationships: {src.get('relationship_count')}")
        lines.append("")

    pc = report.get("projected_counts", {})
    lines.append("Projected counts:")
    lines.append(f"  sources        : {pc.get('sources')}")
    lines.append(f"  nodes          : {pc.get('nodes')}")
    for t, c in sorted((pc.get("nodes_by_type") or {}).items()):
        lines.append(f"    {t:>12}: {c}")
    lines.append(f"  relationships  : {pc.get('relationships')}")
    for t, c in sorted((pc.get("relationships_by_type") or {}).items()):
        lines.append(f"    {t:>12}: {c}")
    lines.append("")

    rel = report.get("relationships", {})
    lines.append(f"Dangling relationship targets: {rel.get('dangling_targets')} "
                 "(allowed)")
    lines.append(f"Skipped relationships (unresolvable source): "
                 f"{rel.get('skipped')}")
    lines.append("")

    prov = report.get("provenance_coverage", {})
    lines.append(f"Provenance coverage: "
                 f"{prov.get('nodes_with_provenance')}/{prov.get('total_nodes')} "
                 f"({round(prov.get('coverage', 0) * 100, 2)}%)")
    lines.append("")

    conflicts = report.get("conflicts", {})
    lines.append("Conflicts:")
    for k, v in sorted(conflicts.items()):
        lines.append(f"  {k}: {v}")
    lines.append("")

    held = report.get("skipped_held_records") or {}
    if held:
        lines.append("Skipped / held records (not in the plan):")
        lines.append(f"  held    : {held.get('held')}")
        lines.append(f"  rejected: {held.get('rejected')}")
        lines.append("")

    if report.get("simulated"):
        lines.append(f"Simulated: YES (isolated in-memory repository, "
                     f"rollback={'verified' if report.get('rollback') else 'n/a'})")
    else:
        lines.append("Simulated: NO (static verification only)")

    return "\n".join(lines) + "\n"
