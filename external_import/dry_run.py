"""Dry-run simulation for external import data against the production database.

The dry run opens the production database in **read-only** mode (``file:...?mode=ro``)
and compares the external data against existing nodes, sources, and relationships.
It produces a deterministic report classifying every item as new, existing-match,
or existing-conflict.  No data is ever written, modified, or deleted.

Determinism: same input + same DB state = byte-identical report.  No timestamps,
no random IDs, no environment-dependent ordering.
"""

import hashlib
import json
import os
import sqlite3
from collections import Counter
from dataclasses import dataclass, field

from external_import.validator import (
    validate_external,
    ExternalValidationResult,
)
from retrieval.repository import DEFAULT_KNOWLEDGE_DB


def _content_hash(data):
    """Deterministic SHA-256 of JSON-serialized *data* (sorted keys)."""
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _read_only_conn(db_path):
    """Open a read-only SQLite connection.  Raises if file does not exist."""
    if not isinstance(db_path, str) or not db_path:
        raise ValueError("db_path must be a non-empty string")
    if not os.path.isfile(db_path):
        raise FileNotFoundError(f"database not found: {db_path!r}")
    return sqlite3.connect(
        f"file:{db_path}?mode=ro", uri=True,
        check_same_thread=False,
    )


def _get_existing_sources(conn):
    """Return {source_name: {id, name, version, location}} for all sources."""
    rows = conn.execute(
        "SELECT id, name, version, location FROM sources").fetchall()
    return {r[1]: {"id": r[0], "name": r[1], "version": r[2],
                    "location": r[3]} for r in rows}


def _get_existing_nodes(conn):
    """Return {node_id: {id, type, name, description, metadata}} for all nodes."""
    rows = conn.execute(
        "SELECT id, type, name, description, metadata FROM nodes").fetchall()
    nodes = {}
    for r in rows:
        meta = json.loads(r[4]) if r[4] else {}
        nodes[r[0]] = {
            "id": r[0], "type": r[1], "name": r[2],
            "description": r[3], "metadata": meta,
        }
    return nodes


def _get_existing_relationships(conn):
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


@dataclass
class DryRunReport:
    """Deterministic machine-readable dry-run report."""
    safe: bool
    validated: bool
    db_opened: bool

    # source summary
    source_name: str = ""
    source_version: str = None

    # totals
    nodes_total: int = 0
    relationships_total: int = 0

    # node classification
    new_nodes: int = 0
    existing_nodes: int = 0
    content_match: int = 0
    content_mismatch: int = 0
    conflicting_nodes: list = field(default_factory=list)

    # relationship classification
    new_relationships: int = 0
    existing_relationships: int = 0
    duplicate_relationships: int = 0

    # source classification
    new_source: bool = True
    existing_source: bool = False

    # errors and warnings
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    # invalid items
    invalid_items: int = 0

    def as_dict(self):
        return {
            "safe": self.safe,
            "validated": self.validated,
            "db_opened": self.db_opened,
            "source": {
                "name": self.source_name,
                "version": self.source_version,
            },
            "nodes_total": self.nodes_total,
            "relationships_total": self.relationships_total,
            "new_nodes": self.new_nodes,
            "existing_nodes": self.existing_nodes,
            "content_match": self.content_match,
            "content_mismatch": self.content_mismatch,
            "conflicting_nodes": sorted(self.conflicting_nodes),
            "new_relationships": self.new_relationships,
            "existing_relationships": self.existing_relationships,
            "duplicate_relationships": self.duplicate_relationships,
            "new_source": self.new_source,
            "existing_source": self.existing_source,
            "invalid_items": self.invalid_items,
            "errors": sorted(self.errors, key=lambda e: (e.get("code", ""), e.get("path", ""))),
            "warnings": sorted(self.warnings, key=lambda w: (w.get("code", ""), w.get("path", ""))),
        }

    def as_json(self):
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2,
                          sort_keys=True)


def _human_summary(report):
    """Render the report as a compact human-readable block."""
    lines = []
    lines.append("External import dry-run")
    lines.append("======================")
    lines.append(f"SAFE: {'YES' if report.safe else 'NO'}")
    lines.append("")

    if report.errors:
        lines.append(f"Errors: {len(report.errors)}")
        for e in report.errors[:10]:
            lines.append(f"  - [{e.get('code')}] {e.get('message')}")
        if len(report.errors) > 10:
            lines.append(f"  ... and {len(report.errors) - 10} more")
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

    lines.append("Nodes:")
    lines.append(f"  total        : {report.nodes_total}")
    lines.append(f"  new          : {report.new_nodes}")
    lines.append(f"  existing     : {report.existing_nodes}")
    if report.existing_nodes > 0:
        lines.append(f"    match      : {report.content_match}")
        lines.append(f"    mismatch   : {report.content_mismatch}")
    lines.append(f"  conflicting  : {len(report.conflicting_nodes)}")
    lines.append("")

    lines.append("Relationships:")
    lines.append(f"  total        : {report.relationships_total}")
    lines.append(f"  new          : {report.new_relationships}")
    lines.append(f"  existing     : {report.existing_relationships}")
    lines.append(f"  duplicates   : {report.duplicate_relationships}")
    lines.append("")

    lines.append(f"Source status  : {'NEW' if report.new_source else 'EXISTS (same name)'}")
    lines.append(f"Invalid items  : {report.invalid_items}")
    lines.append("")

    lines.append(f"DB opened (read-only): {report.db_opened}")
    return "\n".join(lines) + "\n"


def dry_run(data, db_path=None):
    """Run validation and conflict detection without any mutation.

    1. Validate the external data (structure, types, referential integrity).
    2. Open the production DB in read-only mode.
    3. Classify every node as new / existing-match / existing-conflict.
    4. Classify every relationship as new / existing.
    5. Return a deterministic :class:`DryRunReport`.

    The production database is NEVER opened in write mode and NEVER modified.
    """
    db_path = db_path or DEFAULT_KNOWLEDGE_DB

    # Step 1: validate
    if isinstance(data, dict):
        validation = validate_external(data)
    else:
        return DryRunReport(
            safe=False, validated=False, db_opened=False,
            errors=[{"code": "invalid_input",
                     "message": "input must be a JSON object", "path": ""}],
        )

    report = DryRunReport(
        safe=False,
        validated=validation.valid,
        db_opened=False,
        errors=[e.as_dict() for e in validation.errors],
        warnings=[w.as_dict() for w in validation.warnings],
        invalid_items=len(validation.errors),
    )

    if not validation.valid:
        return report

    src = validation.source or {}
    report.source_name = src.get("name", "")
    report.source_version = src.get("version")
    report.nodes_total = len(validation.nodes)
    report.relationships_total = len(validation.relationships)

    # Step 2: open production DB read-only
    try:
        conn = _read_only_conn(db_path)
        report.db_opened = True
    except (FileNotFoundError, ValueError) as e:
        report.errors.append({"code": "db_error",
                              "message": str(e), "path": db_path})
        report.warnings.append({"code": "db_unavailable",
                                "message": "production DB not available; "
                                           "conflict detection skipped",
                                "path": db_path})
        # Even without DB, report validated data
        report.new_nodes = report.nodes_total
        report.new_relationships = report.relationships_total
        report.safe = True
        return report

    try:
        existing_sources = _get_existing_sources(conn)
        existing_nodes = _get_existing_nodes(conn)
        existing_rels = _get_existing_relationships(conn)
    finally:
        conn.close()

    # Step 3: classify nodes
    new_count = 0
    existing_count = 0
    match_count = 0
    mismatch_count = 0
    conflicting = []

    for node in validation.nodes:
        nid = node["id"]
        if nid in existing_nodes:
            existing_count += 1
            existing = existing_nodes[nid]
            # Compare content (type, name, description)
            existing_content = {
                "type": existing["type"],
                "name": existing["name"],
                "description": existing["description"],
            }
            new_content = {
                "type": node["type"],
                "name": node["name"],
                "description": node["description"],
            }
            if existing_content == new_content:
                match_count += 1
            else:
                mismatch_count += 1
                conflicting.append(nid)
        else:
            new_count += 1

    report.new_nodes = new_count
    report.existing_nodes = existing_count
    report.content_match = match_count
    report.content_mismatch = mismatch_count
    report.conflicting_nodes = conflicting

    # Step 4: classify relationships
    new_rels = 0
    existing_rels_count = 0
    for rel in validation.relationships:
        key = (rel["source_node_id"], rel["relationship_type"],
               rel["target_node_id"])
        if key in existing_rels:
            existing_rels_count += 1
        else:
            new_rels += 1

    report.new_relationships = new_rels
    report.existing_relationships = existing_rels_count

    # Step 5: classify source
    source_name = report.source_name
    if source_name in existing_sources:
        report.new_source = False
        report.existing_source = True
    else:
        report.new_source = True
        report.existing_source = False

    # Safe = no errors and no content conflicts
    report.safe = (report.validated and report.errors == []
                   and report.content_mismatch == 0)

    return report
