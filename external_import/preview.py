"""Preview report for a staged external import.

Reads a staging database and compares against the production database to
produce a deterministic, human-readable report of what the apply step would
change.  No data is written or modified — purely read-only.

The staging database contains only NEW items (no conflicts, no duplicates).
The preview adds context by classifying each staged item against production:
  - Nodes: new vs. already-in-production (identical)
  - Relationships: new vs. already-in-production
"""

import json
import os
import sqlite3
from dataclasses import dataclass, field

from external_import.dry_run import (
    _read_only_conn,
    _get_existing_nodes,
    _get_existing_relationships,
    _get_existing_sources,
)


def _staging_conn(staging_path):
    """Open a read-only connection to the staging database."""
    if not os.path.isfile(staging_path):
        raise FileNotFoundError(f"staging database not found: {staging_path!r}")
    return sqlite3.connect(
        f"file:{staging_path}?mode=ro", uri=True,
        check_same_thread=False,
    )


def _read_staging_nodes(conn):
    """Return {node_id: {id, type, name, description, metadata, source_id}}."""
    rows = conn.execute(
        "SELECT id, type, name, description, metadata, source_id "
        "FROM nodes").fetchall()
    nodes = {}
    for r in rows:
        meta = json.loads(r[4]) if r[4] else {}
        nodes[r[0]] = {
            "id": r[0], "type": r[1], "name": r[2],
            "description": r[3], "metadata": meta, "source_id": r[5],
        }
    return nodes


def _read_staging_rels(conn):
    """Return {(src, type, tgt): {source_node_id, relationship_type, target_node_id, label}}."""
    rows = conn.execute(
        "SELECT source_node_id, relationship_type, target_node_id, label "
        "FROM relationships").fetchall()
    rels = {}
    for r in rows:
        key = (r[0], r[1], r[2])
        rels[key] = {
            "source_node_id": r[0], "relationship_type": r[1],
            "target_node_id": r[2], "label": r[3],
        }
    return rels


def _read_staging_source(conn):
    """Return source info from staging DB."""
    rows = conn.execute(
        "SELECT id, name, version, location, imported_at FROM sources"
    ).fetchall()
    if not rows:
        return None
    return {"id": rows[0][0], "name": rows[0][1], "version": rows[0][2],
            "location": rows[0][3], "imported_at": rows[0][4]}


@dataclass
class PreviewReport:
    """Deterministic machine-readable preview of a staged import."""
    safe: bool
    staging_valid: bool
    production_available: bool

    # source
    source_name: str = ""
    source_version: str = None

    # staging counts
    staged_nodes: int = 0
    staged_relationships: int = 0

    # node classification (staged vs production)
    new_nodes: int = 0
    nodes_already_in_production: int = 0

    # relationship classification
    new_relationships: int = 0
    rels_already_in_production: int = 0

    # production state
    production_node_count: int = 0
    production_relationship_count: int = 0
    production_source_count: int = 0

    # projected state after apply
    projected_node_count: int = 0
    projected_relationship_count: int = 0

    # warnings
    warnings: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def as_dict(self):
        return {
            "safe": self.safe,
            "staging_valid": self.staging_valid,
            "production_available": self.production_available,
            "source": {
                "name": self.source_name,
                "version": self.source_version,
            },
            "staged_nodes": self.staged_nodes,
            "staged_relationships": self.staged_relationships,
            "new_nodes": self.new_nodes,
            "nodes_already_in_production": self.nodes_already_in_production,
            "new_relationships": self.new_relationships,
            "rels_already_in_production": self.rels_already_in_production,
            "production": {
                "node_count": self.production_node_count,
                "relationship_count": self.production_relationship_count,
                "source_count": self.production_source_count,
            },
            "projected": {
                "node_count": self.projected_node_count,
                "relationship_count": self.projected_relationship_count,
            },
            "warnings": sorted(self.warnings, key=lambda w: (w.get("code", ""), w.get("path", ""))),
            "errors": sorted(self.errors, key=lambda e: (e.get("code", ""), e.get("path", ""))),
        }

    def as_json(self):
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def preview(staging_path, production_db_path=None):
    """Generate a preview report from a staging database.

    Reads the staging DB and production DB (both read-only) and classifies
    every staged item.  No data is written or modified.
    """
    from external_import.staging import StagingResult
    from external_import.dry_run import DEFAULT_KNOWLEDGE_DB

    production_db_path = production_db_path or DEFAULT_KNOWLEDGE_DB

    result = PreviewReport(safe=False, staging_valid=False,
                           production_available=False)

    # Open staging DB
    try:
        s_conn = _staging_conn(staging_path)
    except FileNotFoundError as e:
        result.errors.append({"code": "staging_not_found",
                              "message": str(e), "path": staging_path})
        return result

    try:
        staging_nodes = _read_staging_nodes(s_conn)
        staging_rels = _read_staging_rels(s_conn)
        staging_source = _read_staging_source(s_conn)
    finally:
        s_conn.close()

    result.staging_valid = True
    result.staged_nodes = len(staging_nodes)
    result.staged_relationships = len(staging_rels)

    if staging_source:
        result.source_name = staging_source.get("name", "")
        result.source_version = staging_source.get("version")

    # Open production DB
    prod_conn = None
    try:
        prod_conn = _read_only_conn(production_db_path)
        result.production_available = True
        existing_nodes = _get_existing_nodes(prod_conn)
        existing_rels = _get_existing_relationships(prod_conn)
        prod_sources = _get_existing_sources(prod_conn)
        result.production_node_count = prod_conn.execute(
            "SELECT COUNT(*) FROM nodes").fetchone()[0]
        result.production_relationship_count = prod_conn.execute(
            "SELECT COUNT(*) FROM relationships").fetchone()[0]
        result.production_source_count = len(prod_sources)
    except (FileNotFoundError, ValueError):
        result.warnings.append({"code": "db_unavailable",
                                "message": "production DB not available; "
                                           "preview limited to staging data",
                                "path": production_db_path})
        existing_nodes = {}
        existing_rels = {}
    finally:
        if prod_conn is not None:
            prod_conn.close()

    # Classify staged nodes
    new_count = 0
    already_count = 0
    for nid, node in staging_nodes.items():
        if nid in existing_nodes:
            already_count += 1
        else:
            new_count += 1

    result.new_nodes = new_count
    result.nodes_already_in_production = already_count

    # Classify staged relationships
    new_rels = 0
    exist_rels = 0
    for key, rel in staging_rels.items():
        if key in existing_rels:
            exist_rels += 1
        else:
            new_rels += 1

    result.new_relationships = new_rels
    result.rels_already_in_production = exist_rels

    # Projected state
    result.projected_node_count = result.production_node_count + new_count
    result.projected_relationship_count = (
        result.production_relationship_count + new_rels)

    result.safe = result.staging_valid and not result.errors
    return result


def human_preview_summary(report):
    """Render the preview report as a compact human-readable block."""
    lines = []
    lines.append("External import preview")
    lines.append("======================")
    lines.append(f"SAFE TO APPLY: {'YES' if report.safe else 'NO'}")
    lines.append("")

    if report.errors:
        lines.append(f"Errors: {len(report.errors)}")
        for e in report.errors[:10]:
            lines.append(f"  - [{e.get('code')}] {e.get('message')}")
        lines.append("")

    if report.warnings:
        lines.append(f"Warnings: {len(report.warnings)}")
        for w in report.warnings[:10]:
            lines.append(f"  - [{w.get('code')}] {w.get('message')}")
        lines.append("")

    lines.append(f"Source: {report.source_name}")
    if report.source_version:
        lines.append(f"  version: {report.source_version}")
    lines.append("")

    lines.append("Production (current):")
    lines.append(f"  sources      : {report.production_source_count}")
    lines.append(f"  nodes        : {report.production_node_count}")
    lines.append(f"  relationships: {report.production_relationship_count}")
    lines.append("")

    lines.append("Staged for import:")
    lines.append(f"  nodes to add      : {report.new_nodes}")
    if report.nodes_already_in_production:
        lines.append(f"  nodes unchanged   : {report.nodes_already_in_production} (already in production)")
    lines.append(f"  relationships to add: {report.new_relationships}")
    if report.rels_already_in_production:
        lines.append(f"  rels unchanged    : {report.rels_already_in_production} (already in production)")
    lines.append("")

    lines.append("After apply (projected):")
    lines.append(f"  nodes        : {report.projected_node_count}")
    lines.append(f"  relationships: {report.projected_relationship_count}")
    return "\n".join(lines) + "\n"
