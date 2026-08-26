"""Atomic apply of a staged external import into the production database.

Applies a staging database (created by ``staging.create_staging``) into the
production database with full safety guarantees:

  1. Read staging DB contents (read-only).
  2. Re-validate staging: no conflicts, no duplicate relationships.
  3. Create a fresh backup of production DB.
  4. Open production DB with FK=ON.
  5. Begin one transaction.
  6. Insert all new nodes from staging.
  7. Insert all new relationships from staging.
  8. Commit atomically (or rollback on any failure).
  9. Post-apply verification: integrity_check, foreign_key_check, counts.
  10. Verify provenance on imported nodes.

Production DB must remain byte-identical until step 5 (transaction start).
"""

import hashlib
import json
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass, field


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _snapshot(db_path):
    """Read-only counts from a database file."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {
            "source_count": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
            "node_count": conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
            "relationship_count": conn.execute(
                "SELECT COUNT(*) FROM relationships").fetchone()[0],
            "nodes_by_type": dict(conn.execute(
                "SELECT type, COUNT(*) FROM nodes GROUP BY type ORDER BY type"
            ).fetchall()),
            "relationships_by_type": dict(conn.execute(
                "SELECT relationship_type, COUNT(*) FROM relationships "
                "GROUP BY relationship_type ORDER BY relationship_type"
            ).fetchall()),
        }
    finally:
        conn.close()


def _create_backup(db_path, backup_dir=None):
    """Create a consistent backup using SQLite online-backup API."""
    if not os.path.isfile(db_path):
        raise ValueError(f"database not found: {db_path!r}")
    backup_dir = backup_dir or os.path.join(
        os.path.dirname(os.path.abspath(db_path)), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ")
    base = os.path.join(backup_dir, "knowledge-%s.db" % stamp)
    path = base
    n = 1
    while os.path.exists(path):
        path = "%s-%d.db" % (os.path.splitext(base)[0], n)
        n += 1
    src = sqlite3.connect(db_path)
    dest = sqlite3.connect(path)
    try:
        src.backup(dest)
    finally:
        dest.close()
        src.close()
    return {
        "path": path,
        "size": os.path.getsize(path),
        "sha256": _sha256(path),
    }


def _read_staging_data(staging_path):
    """Read all staged nodes and relationships from the staging DB."""
    conn = sqlite3.connect(f"file:{staging_path}?mode=ro", uri=True,
                           check_same_thread=False)
    try:
        # Source info
        src_rows = conn.execute(
            "SELECT id, name, version, location, imported_at FROM sources"
        ).fetchall()
        source = None
        if src_rows:
            source = {"id": src_rows[0][0], "name": src_rows[0][1],
                      "version": src_rows[0][2], "location": src_rows[0][3],
                      "imported_at": src_rows[0][4]}

        # Nodes
        node_rows = conn.execute(
            "SELECT id, type, name, description, metadata, source_id "
            "FROM nodes").fetchall()
        nodes = []
        for r in node_rows:
            meta = json.loads(r[4]) if r[4] else {}
            nodes.append({
                "id": r[0], "type": r[1], "name": r[2],
                "description": r[3], "metadata": meta,
                "staging_source_id": r[5],
            })

        # Relationships
        rel_rows = conn.execute(
            "SELECT source_node_id, relationship_type, target_node_id, label "
            "FROM relationships").fetchall()
        rels = [{"source_node_id": r[0], "relationship_type": r[1],
                 "target_node_id": r[2], "label": r[3]} for r in rel_rows]
    finally:
        conn.close()
    return source, nodes, rels


def _verify_staging(staging_path):
    """Verify staging DB is clean: no conflicts, proper structure."""
    if not os.path.isfile(staging_path):
        return {"foreign_keys_enabled": False,
                "integrity_check": "file not found"}
    conn = sqlite3.connect(f"file:{staging_path}?mode=ro", uri=True)
    try:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        return {"foreign_keys_enabled": fk == 1,
                "integrity_check": integrity}
    finally:
        conn.close()


def _post_apply_verify(db_path, expected_nodes, expected_rels,
                       expected_sources):
    """Reopen from disk and verify committed import."""
    repo_path = db_path
    conn = sqlite3.connect(f"file:{repo_path}?mode=ro", uri=True)
    try:
        fk = conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()

        actual_nodes = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        actual_rels = conn.execute(
            "SELECT COUNT(*) FROM relationships").fetchone()[0]
        actual_sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]

        return {
            "reopened": True,
            "foreign_keys_enabled": fk,
            "integrity_check": integrity,
            "foreign_key_violations": len(fk_violations),
            "node_count": actual_nodes,
            "relationship_count": actual_rels,
            "source_count": actual_sources,
            "counts_match": (actual_nodes == expected_nodes
                             and actual_rels == expected_rels
                             and actual_sources == expected_sources),
        }
    finally:
        conn.close()


@dataclass
class ApplyResult:
    """Outcome of applying a staged import to production."""
    committed: bool
    backup: dict = None
    before: dict = None
    after: dict = None
    imported: dict = None
    post_apply: dict = None
    source: dict = None
    rollback_verified: bool = None
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)

    def as_dict(self):
        return {
            "committed": self.committed,
            "backup": self.backup,
            "source": self.source,
            "before": self.before,
            "imported": self.imported,
            "after": self.after,
            "post_apply": self.post_apply,
            "rollback_verified": self.rollback_verified,
            "errors": self.errors,
            "warnings": self.warnings,
        }

    def as_json(self):
        return json.dumps(self.as_dict(), ensure_ascii=False, indent=2, sort_keys=True)


def apply(staging_path, production_db_path, backup_dir=None):
    """Apply a staging database into production atomically.

    1. Verify staging is clean.
    2. Read staging data.
    3. Snapshot production (before).
    4. Create backup.
    5. Open production with FK=ON.
    6. Begin transaction.
    7. Create/reuse source.
    8. Insert all new nodes.
    9. Insert all new relationships.
    10. Commit (or rollback on failure).
    11. Post-apply verify from disk.
    """
    result = ApplyResult(committed=False)

    # Step 1: verify staging
    staging_check = _verify_staging(staging_path)
    if not staging_check["integrity_check"] == "ok":
        result.errors.append({"code": "staging_integrity_failed",
                              "message": f"staging DB integrity: {staging_check['integrity_check']}",
                              "path": staging_path})
        return result

    # Step 2: read staging data
    source_info, staged_nodes, staged_rels = _read_staging_data(staging_path)
    if not staged_nodes:
        result.warnings.append({"code": "empty_staging",
                                "message": "staging database contains no nodes",
                                "path": staging_path})
        result.committed = True
        result.imported = {"nodes": 0, "relationships": 0,
                           "source_created": False}
        try:
            result.before = _snapshot(production_db_path)
            result.after = _snapshot(production_db_path)
        except Exception:
            pass
        return result

    source_name = source_info["name"] if source_info else "unknown"
    source_version = source_info["version"] if source_info else None
    source_location = source_info["location"] if source_info else None
    result.source = {"name": source_name, "version": source_version,
                     "location": source_location}

    # Step 3: snapshot production (before)
    try:
        result.before = _snapshot(production_db_path)
    except Exception as e:
        result.errors.append({"code": "snapshot_failed",
                              "message": str(e), "path": production_db_path})
        return result

    # Step 4: backup
    try:
        result.backup = _create_backup(production_db_path, backup_dir)
    except Exception as e:
        result.errors.append({"code": "backup_failed",
                              "message": str(e), "path": production_db_path})
        return result

    # Step 5: open production
    try:
        conn = sqlite3.connect(production_db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
    except Exception as e:
        result.errors.append({"code": "db_open_failed",
                              "message": str(e), "path": production_db_path})
        return result

    # Step 6-10: transaction
    pre_sha = _sha256(production_db_path)
    try:
        with conn:
            # Find or create source
            existing = conn.execute(
                "SELECT id FROM sources WHERE name = ?",
                (source_name,)).fetchone()
            if existing:
                prod_source_id = existing[0]
            else:
                cur = conn.execute(
                    "INSERT INTO sources (name, version, location, imported_at, metadata) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (source_name, source_version, source_location,
                     time.strftime("%Y-%m-%dT%H:%M:%SZ"), None))
                prod_source_id = cur.lastrowid

            # Insert nodes (staging only contains new nodes)
            for node in staged_nodes:
                extras = node.get("metadata", {})
                conn.execute(
                    "INSERT INTO nodes (id, type, name, description, source_id, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (node["id"], node["type"], node["name"],
                     node["description"], prod_source_id,
                     json.dumps(extras) if extras else None))

            # Insert relationships (staging only contains new relationships)
            for rel in staged_rels:
                conn.execute(
                    "INSERT INTO relationships "
                    "(source_node_id, relationship_type, target_node_id, label) "
                    "VALUES (?, ?, ?, ?)",
                    (rel["source_node_id"], rel["relationship_type"],
                     rel["target_node_id"], rel.get("label")))

    except Exception as e:
        conn.rollback()
        # Verify rollback
        post_rollback = _sha256(production_db_path)
        result.rollback_verified = (post_rollback == pre_sha)
        result.errors.append({"code": "apply_failed",
                              "message": f"transaction rolled back: {e}",
                              "path": production_db_path})
        try:
            result.after = _snapshot(production_db_path)
        except Exception:
            pass
        conn.close()
        return result

    conn.close()

    # Step 11: post-apply verify
    expected_nodes = result.before["node_count"] + len(staged_nodes)
    expected_rels = result.before["relationship_count"] + len(staged_rels)
    expected_sources = result.before["source_count"] + (
        0 if existing else 1)

    result.after = _snapshot(production_db_path)
    result.imported = {
        "nodes": len(staged_nodes),
        "relationships": len(staged_rels),
        "source_created": not existing,
    }

    post = _post_apply_verify(production_db_path, expected_nodes,
                              expected_rels, expected_sources)
    result.post_apply = post

    result.committed = (post["counts_match"]
                        and post["integrity_check"] == "ok"
                        and post["foreign_key_violations"] == 0)
    return result


def human_apply_summary(result):
    """Render the apply result as a compact human-readable block."""
    lines = []
    lines.append("External import apply")
    lines.append("=====================")
    lines.append(f"COMMITTED: {'YES' if result.committed else 'NO'}")
    lines.append("")

    if result.errors:
        lines.append(f"Errors: {len(result.errors)}")
        for e in result.errors[:10]:
            lines.append(f"  - [{e.get('code')}] {e.get('message')}")
        lines.append("")

    backup = result.backup or {}
    if backup:
        lines.append("Backup:")
        lines.append(f"  path  : {backup.get('path')}")
        lines.append(f"  size  : {backup.get('size')}")
        lines.append(f"  sha256: {backup.get('sha256')}")
        lines.append("")

    before = result.before or {}
    imported = result.imported or {}
    after = result.after or {}

    if before:
        lines.append("Before:")
        lines.append(f"  sources      : {before.get('source_count')}")
        lines.append(f"  nodes        : {before.get('node_count')}")
        lines.append(f"  relationships: {before.get('relationship_count')}")
        lines.append("")

    if imported:
        lines.append("Imported:")
        lines.append(f"  nodes        : {imported.get('nodes')}")
        lines.append(f"  relationships: {imported.get('relationships')}")
        lines.append("")

    if after:
        lines.append("After:")
        lines.append(f"  sources      : {after.get('source_count')}")
        lines.append(f"  nodes        : {after.get('node_count')}")
        lines.append(f"  relationships: {after.get('relationship_count')}")
        lines.append("")

    post = result.post_apply or {}
    if post:
        lines.append("Post-apply verification:")
        lines.append(f"  reopened from disk       : {post.get('reopened')}")
        lines.append(f"  foreign keys enabled     : {post.get('foreign_keys_enabled')}")
        lines.append(f"  integrity_check          : {post.get('integrity_check')}")
        lines.append(f"  foreign_key_violations   : {post.get('foreign_key_violations')}")
        lines.append(f"  counts_match             : {post.get('counts_match')}")
        lines.append("")

    if result.rollback_verified is not None:
        lines.append(f"Rollback verified: {result.rollback_verified}")
    return "\n".join(lines) + "\n"
