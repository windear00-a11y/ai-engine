"""Staging database creation for external import.

Creates a disposable, file-backed SQLite database containing proposed import
items (nodes and relationships) that do NOT conflict with the production
database.  The staging DB is a snapshot of what would be imported — never
the production DB itself.

Design:
  1. Validate external JSON (reuse ``external_import.validator``).
  2. Open production DB read-only to detect existing nodes/relationships.
  3. Create staging DB with the same schema as production.
  4. Copy only NEW, non-conflicting items into staging.
  5. Record metadata: source info, pre-apply state, timestamps.

Staging DB is deterministic: same input + same DB state = identical staging.
"""

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field

from external_import.validator import validate_external
from external_import.dry_run import (
    _read_only_conn,
    _get_existing_nodes,
    _get_existing_relationships,
    _get_existing_sources,
    _content_hash,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    version     TEXT,
    location    TEXT,
    imported_at TEXT NOT NULL,
    metadata    TEXT
);

CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    name        TEXT,
    description TEXT,
    source_id   INTEGER,
    metadata    TEXT,
    FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS relationships (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    source_node_id   TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    target_node_id   TEXT NOT NULL,
    label            TEXT,
    FOREIGN KEY (source_node_id) REFERENCES nodes(id) ON DELETE CASCADE,
    UNIQUE (source_node_id, relationship_type, target_node_id)
);

CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_rel_source ON relationships(source_node_id);
CREATE INDEX IF NOT EXISTS idx_rel_target ON relationships(target_node_id);
"""


@dataclass
class StagingResult:
    """Outcome of creating a staging database."""
    success: bool
    staging_path: str = ""
    source_name: str = ""
    source_version: str = None
    production_db_path: str = ""
    nodes_staged: int = 0
    nodes_skipped: int = 0
    nodes_conflicting: int = 0
    conflicting_node_ids: list = field(default_factory=list)
    relationships_staged: int = 0
    relationships_skipped: int = 0
    relationships_new_source_id: int = None
    source_created: bool = False
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    pre_apply_state: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            "success": self.success,
            "staging_path": self.staging_path,
            "source": {
                "name": self.source_name,
                "version": self.source_version,
            },
            "production_db": self.production_db_path,
            "nodes_staged": self.nodes_staged,
            "nodes_skipped": self.nodes_skipped,
            "nodes_conflicting": self.nodes_conflicting,
            "conflicting_node_ids": sorted(self.conflicting_node_ids),
            "relationships_staged": self.relationships_staged,
            "relationships_skipped": self.relationships_skipped,
            "source_created": self.source_created,
            "pre_apply_state": self.pre_apply_state,
            "errors": sorted(self.errors, key=lambda e: (e.get("code", ""), e.get("path", ""))),
            "warnings": sorted(self.warnings, key=lambda w: (w.get("code", ""), w.get("path", ""))),
        }

    def as_json(self):
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def _read_prod_snapshot(prod_conn):
    """Read counts from production DB (read-only connection)."""
    return {
        "source_count": prod_conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
        "node_count": prod_conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
        "relationship_count": prod_conn.execute("SELECT COUNT(*) FROM relationships").fetchone()[0],
    }


def create_staging(data, staging_path, production_db_path):
    """Create a staging database with proposed import items.

    1. Validate the external data.
    2. Open production DB read-only.
    3. Create staging DB at ``staging_path``.
    4. Classify each node: new / existing-match (skip) / existing-conflict (skip).
    5. Classify each relationship: new / existing (skip).
    6. Copy only new items into staging.
    7. Return a :class:`StagingResult`.

    The production database is NEVER opened in write mode and NEVER modified.
    """
    result = StagingResult(success=False, staging_path=staging_path,
                           production_db_path=production_db_path)

    # Step 1: validate
    if not isinstance(data, dict):
        result.errors.append({"code": "invalid_input",
                              "message": "input must be a JSON object", "path": ""})
        return result

    validation = validate_external(data)
    result.warnings = [w.as_dict() for w in validation.warnings]

    if not validation.valid:
        result.errors = [e.as_dict() for e in validation.errors]
        return result

    src = validation.source or {}
    result.source_name = src.get("name", "")
    result.source_version = src.get("version")

    # Step 2: open production DB read-only
    prod_conn = None
    try:
        prod_conn = _read_only_conn(production_db_path)
        existing_nodes = _get_existing_nodes(prod_conn)
        existing_rels = _get_existing_relationships(prod_conn)
        existing_sources = _get_existing_sources(prod_conn)
        result.pre_apply_state = _read_prod_snapshot(prod_conn)
    except (FileNotFoundError, ValueError) as e:
        result.errors.append({"code": "db_error",
                              "message": str(e), "path": production_db_path})
        result.warnings.append({"code": "db_unavailable",
                                "message": "production DB not available; "
                                           "conflict detection skipped",
                                "path": production_db_path})
        existing_nodes = {}
        existing_rels = {}
        existing_sources = {}
        result.pre_apply_state = {"source_count": 0, "node_count": 0,
                                  "relationship_count": 0}
    finally:
        if prod_conn is not None:
            prod_conn.close()

    # Step 3: create staging DB
    staging_dir = os.path.dirname(staging_path)
    if staging_dir:
        os.makedirs(staging_dir, exist_ok=True)

    staging_conn = sqlite3.connect(staging_path, check_same_thread=False)
    staging_conn.row_factory = sqlite3.Row
    staging_conn.execute("PRAGMA foreign_keys=ON")
    try:
        staging_conn.executescript(_SCHEMA)
    except Exception:
        staging_conn.close()
        raise

    try:
        # Step 4: find or create source
        source_name = src.get("name", "")
        source_location = src.get("location")
        source_version = src.get("version")

        existing_source_id = None
        if source_name in existing_sources:
            existing_source_id = existing_sources[source_name]["id"]

        if existing_source_id is not None:
            source_id = existing_source_id
            result.source_created = False
        else:
            cur = staging_conn.execute(
                "INSERT INTO sources (name, version, location, imported_at, metadata) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_name, source_version, source_location,
                 time.strftime("%Y-%m-%dT%H:%M:%SZ"), None))
            source_id = cur.lastrowid
            result.source_created = True

        # Step 5: classify and stage nodes
        node_content_cache = {}
        for node in validation.nodes:
            nid = node["id"]
            if nid in existing_nodes:
                existing = existing_nodes[nid]
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
                    result.nodes_skipped += 1
                else:
                    result.nodes_conflicting += 1
                    result.conflicting_node_ids.append(nid)
            else:
                extras = node.get("metadata", {})
                staging_conn.execute(
                    "INSERT INTO nodes (id, type, name, description, source_id, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (nid, node["type"], node["name"], node["description"],
                     source_id,
                     json.dumps(extras) if extras else None))
                node_content_cache[nid] = node
                result.nodes_staged += 1

        # Step 6: classify and stage relationships
        for rel in validation.relationships:
            key = (rel["source_node_id"], rel["relationship_type"],
                   rel["target_node_id"])
            if key in existing_rels:
                result.relationships_skipped += 1
            else:
                staging_conn.execute(
                    "INSERT INTO relationships "
                    "(source_node_id, relationship_type, target_node_id, label) "
                    "VALUES (?, ?, ?, ?)",
                    (rel["source_node_id"], rel["relationship_type"],
                     rel["target_node_id"], rel.get("label")))
                result.relationships_staged += 1

        staging_conn.commit()
        result.success = True
    except Exception as e:
        staging_conn.rollback()
        result.errors.append({"code": "staging_error",
                              "message": str(e), "path": staging_path})
    finally:
        staging_conn.close()

    return result


def human_staging_summary(result):
    """Render the staging result as a compact human-readable block."""
    lines = []
    lines.append("External import staging")
    lines.append("=======================")
    lines.append(f"SUCCESS: {'YES' if result.success else 'NO'}")
    lines.append("")

    if result.errors:
        lines.append(f"Errors: {len(result.errors)}")
        for e in result.errors[:10]:
            lines.append(f"  - [{e.get('code')}] {e.get('message')}")
        lines.append("")

    if result.warnings:
        lines.append(f"Warnings: {len(result.warnings)}")
        for w in result.warnings[:10]:
            lines.append(f"  - [{w.get('code')}] {w.get('message')}")
        lines.append("")

    lines.append(f"Source: {result.source_name}")
    if result.source_version:
        lines.append(f"  version: {result.source_version}")
    lines.append("")

    lines.append("Production DB:")
    ps = result.pre_apply_state or {}
    lines.append(f"  sources      : {ps.get('source_count', '?')}")
    lines.append(f"  nodes        : {ps.get('node_count', '?')}")
    lines.append(f"  relationships: {ps.get('relationship_count', '?')}")
    lines.append("")

    lines.append("Staging:")
    lines.append(f"  new nodes staged     : {result.nodes_staged}")
    lines.append(f"  nodes skipped        : {result.nodes_skipped}")
    if result.nodes_conflicting:
        lines.append(f"  nodes conflicting    : {result.nodes_conflicting}")
        for nid in sorted(result.conflicting_node_ids)[:5]:
            lines.append(f"    - {nid}")
        if len(result.conflicting_node_ids) > 5:
            lines.append(f"    ... and {len(result.conflicting_node_ids) - 5} more")
    lines.append(f"  new rels staged      : {result.relationships_staged}")
    lines.append(f"  rels skipped (exist) : {result.relationships_skipped}")
    lines.append(f"  source created       : {result.source_created}")
    lines.append("")

    lines.append(f"Staging DB: {result.staging_path}")
    return "\n".join(lines) + "\n"
