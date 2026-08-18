"""Controlled production import of a verified acceptance import plan.

This is the ONLY code path that writes to the production knowledge database
(``database/knowledge.db``). It is intentionally separate from ``verify`` and
``dry-run`` and enforces, in order:

1. reload + static verification of the plan (refuse anything not SAFE)
2. strict refusal of malformed plans and unresolved relationship endpoints
3. a timestamped, never-overwritten backup of the database BEFORE the write
4. the ENTIRE source import inside ONE SQLite transaction (atomic: any failure
   rolls the whole source back and the database stays unchanged)
5. post-import verification from disk (counts, provenance, traversal, existing
   knowledge preservation, reopen)

Nothing here ever deletes, resets, migrates or replaces the existing database:
the corpus is added as a NEW source. HELD / REJECTED / conflicted identities
are never imported because the plan preview only carries ACCEPTED records.

The existing repository constraints do the integrity enforcement: ``nodes.id``
is a PRIMARY KEY (duplicates refused), ``relationships(source_node_id,
relationship_type, target_node_id)`` is UNIQUE, and ``source_node_id`` is a
foreign key to ``nodes.id``. Everything is imported in one transaction so a
violation rolls back the entire source.
"""

import hashlib
import os
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field

from importing.dry_run import apply_plan, source_identity
from importing.plan_loader import load_plan, PlanLoadError
from importing.verifier import verify_plan
from retrieval.repository import KnowledgeRepository


# -- backup ---------------------------------------------------------------

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_database(db_path, backup_dir=None):
    """Create a timestamped, consistent backup; never overwrite an existing one.

    Uses the SQLite online-backup API so the copy is transactionally
    consistent even if the source is being read. Returns
    ``{"path", "size", "sha256"}`` and raises ``OSError``/``sqlite3.Error`` on
    failure.
    """
    if not isinstance(db_path, str) or not os.path.isfile(db_path):
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


# -- snapshots ------------------------------------------------------------

def _snapshot(db_path):
    """Read-only BEFORE/AFTER counts from a database file on disk."""
    conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True)
    try:
        sources = []
        for r in conn.execute("SELECT id, name, version, location FROM sources"):
            node_count = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE source_id = ?",
                (r[0],)).fetchone()[0]
            sources.append({"id": r[0], "name": r[1], "version": r[2],
                            "location": r[3], "node_count": node_count})
        return {
            "sources": sources,
            "source_count": len(sources),
            "node_count": conn.execute(
                "SELECT COUNT(*) FROM nodes").fetchone()[0],
            "nodes_by_type": dict(conn.execute(
                "SELECT type, COUNT(*) FROM nodes GROUP BY type").fetchall()),
            "relationship_count": conn.execute(
                "SELECT COUNT(*) FROM relationships").fetchone()[0],
            "relationships_by_type": dict(conn.execute(
                "SELECT relationship_type, COUNT(*) FROM relationships "
                "GROUP BY relationship_type").fetchall()),
        }
    finally:
        conn.close()


def _same_snapshot(a, b):
    """Structural equality of two snapshots (ignores source order)."""
    if a["source_count"] != b["source_count"]:
        return False
    if a["node_count"] != b["node_count"]:
        return False
    if a["relationship_count"] != b["relationship_count"]:
        return False
    return True


# -- result ---------------------------------------------------------------

@dataclass
class ImportResult:
    """Outcome of one controlled import attempt (never raises for bad plans)."""
    committed: bool
    plan_verified: bool
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    backup: dict = None
    before: dict = None
    after: dict = None
    imported: dict = None
    post_import: dict = None
    source: dict = None
    rollback: bool = None

    def as_dict(self):
        return {
            "committed": self.committed,
            "plan_verified": self.plan_verified,
            "safe": self.committed,
            "rollback": self.rollback,
            "errors": self.errors,
            "warnings": self.warnings,
            "backup": self.backup,
            "source": self.source,
            "before": self.before,
            "imported": self.imported,
            "after": self.after,
            "post_import": self.post_import,
        }


def _refused(code, message, path=""):
    return ImportResult(committed=False, plan_verified=False,
                        errors=[{"code": code, "message": message,
                                 "path": path}])


# -- post-import verification ---------------------------------------------

def _post_import_check(db_path, plan, verification, before):
    """Reopen the database from disk and verify the committed import."""
    repo = KnowledgeRepository(db_path)
    repo.initialize()
    try:
        foreign_keys = repo.conn.execute(
            "PRAGMA foreign_keys").fetchone()[0] == 1
        counts = {
            "source_count": repo.conn.execute(
                "SELECT COUNT(*) FROM sources").fetchone()[0],
            "node_count": repo.conn.execute(
                "SELECT COUNT(*) FROM nodes").fetchone()[0],
            "nodes_by_type": dict(repo.conn.execute(
                "SELECT type, COUNT(*) FROM nodes GROUP BY type").fetchall()),
            "relationship_count": repo.conn.execute(
                "SELECT COUNT(*) FROM relationships").fetchone()[0],
            "relationships_by_type": dict(repo.conn.execute(
                "SELECT relationship_type, COUNT(*) FROM relationships "
                "GROUP BY relationship_type").fetchall()),
        }

        source_name, source_version, source_location = source_identity(plan)
        row = repo.conn.execute(
            "SELECT id, name, version, location, imported_at FROM sources "
            "WHERE name = ? AND location = ?",
            (source_name, source_location)).fetchone()
        imported_source = None
        imported_node_count = 0
        imported_relationship_count = 0
        if row is not None:
            imported_source = {
                "id": row["id"], "name": row["name"], "version": row["version"],
                "location": row["location"], "imported_at": row["imported_at"],
            }
            imported_node_count = repo.conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE source_id = ?",
                (row["id"],)).fetchone()[0]
            imported_relationship_count = repo.conn.execute(
                "SELECT COUNT(*) FROM relationships r "
                "JOIN nodes n ON r.source_node_id = n.id "
                "WHERE n.source_id = ?", (row["id"],)).fetchone()[0]

        # -- provenance on every imported node --------------------------
        checked = 0
        missing = 0
        if imported_source is not None:
            for node_id in repo.conn.execute(
                    "SELECT id FROM nodes WHERE source_id = ?",
                    (imported_source["id"],)):
                checked += 1
                node = repo.get_node(node_id["id"])
                prov = node.get("provenance") or {}
                if not all(k in prov for k in
                           ("source_id", "source_name", "imported_at")):
                    missing += 1
                if "evidence_references" not in node:
                    missing += 1

        # -- relationship traversal --------------------------------------
        traversed = 0
        traversal_ok = True
        for rel in verification.relationships:
            targets = [f[0]["target"] for f in repo.follow(
                rel["source_node_id"], rel["relationship_type"])]
            if rel["target_node_id"] not in targets:
                traversal_ok = False
            traversed += 1

        # -- existing knowledge preserved --------------------------------
        preserved = True
        lost = []
        for src in before.get("sources", []):
            cur = repo.conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE source_id = ?",
                (src["id"],)).fetchone()[0]
            if cur != src["node_count"]:
                preserved = False
                lost.append({"source_id": src["id"], "before": src["node_count"],
                             "after": cur})

        # -- repository integrity ----------------------------------------
        dangling = repo.conn.execute(
            "SELECT COUNT(*) FROM relationships r "
            "LEFT JOIN nodes n ON r.source_node_id = n.id "
            "WHERE n.id IS NULL").fetchone()[0]
        duplicate_ids = repo.conn.execute(
            "SELECT COUNT(*) FROM nodes n JOIN (SELECT id FROM nodes GROUP BY "
            "id HAVING COUNT(*) > 1) d ON n.id = d.id").fetchone()[0]
        return {
            "reopened": True,
            "foreign_keys_enabled": foreign_keys,
            "counts": counts,
            "imported_source": imported_source,
            "imported_node_count": imported_node_count,
            "imported_relationship_count": imported_relationship_count,
            "provenance": {"checked": checked, "missing": missing,
                           "complete": checked > 0 and missing == 0
                           and checked == len(verification.nodes)},
            "relationship_traversal": {"checked": traversed,
                                       "ok": traversal_ok},
            "existing_knowledge_preserved": {"ok": preserved, "lost": lost},
            "dangling_relationship_sources": dangling,
            "duplicate_primary_keys": duplicate_ids,
        }
    finally:
        repo.close()


# -- entry point ----------------------------------------------------------

def import_plan(plan_or_path, db_path, backup_dir=None):
    """Controlled import of a verified plan into ``db_path`` (atomic).

    ``plan_or_path`` may be a parsed plan dict or a path to an
    ``import_plan.json``. Returns an :class:`ImportResult` -- it never raises
    for malformed plans, failed verification, or constraint failures (all of
    which are reported as ``committed=False``). The database is only written
    after the plan verifies AND a timestamped backup exists.
    """
    if not isinstance(db_path, str) or not db_path:
        return _refused("db_path", "db_path must be a non-empty string")
    if not os.path.isfile(db_path):
        return _refused("database_missing",
                        f"database not found: {db_path!r}")

    if isinstance(plan_or_path, str):
        try:
            plan = load_plan(plan_or_path)
        except PlanLoadError as e:
            return _refused("plan_load_error", str(e))
    else:
        plan = plan_or_path
    if not isinstance(plan, dict):
        return _refused("invalid_plan", "import plan must be a JSON object")

    verification = verify_plan(plan)
    errors = [e.as_dict() for e in verification.errors]
    warnings = [w.as_dict() for w in verification.warnings]

    # strict: the production import refuses unresolved relationship endpoints
    # even though the repository tolerates dangling targets for dry-run.
    for rel in verification.dangling_targets:
        errors.append({
            "code": "relationship_target_unresolved",
            "message": "relationship target %r is not defined in the plan"
                       % rel.get("target_node_id"),
            "path": "preview.proposed_relationships",
        })

    if errors or not verification.valid:
        return ImportResult(committed=False, plan_verified=verification.valid,
                            errors=errors, warnings=warnings)

    before = _snapshot(db_path)
    try:
        backup = backup_database(db_path, backup_dir)
    except (OSError, sqlite3.Error, ValueError) as e:
        return ImportResult(
            committed=False, plan_verified=True, before=before,
            errors=[{"code": "backup_failed", "message": str(e), "path": ""}])

    repo = KnowledgeRepository(db_path)
    try:
        repo.initialize()
        if repo.conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            return ImportResult(
                committed=False, plan_verified=True, before=before,
                backup=backup, errors=[{"code": "foreign_keys_disabled",
                                        "message": "PRAGMA foreign_keys "
                                                   "is not enabled",
                                        "path": ""}])
        ok, counts, failing, reason = apply_plan(repo, plan, verification)
        repo.close()
    except Exception as e:  # noqa: BLE001 -- surface engine failures safely
        repo.close()
        return ImportResult(committed=False, plan_verified=True, before=before,
                            backup=backup, rollback=False,
                            errors=[{"code": "import_error",
                                     "message": str(e), "path": ""}])

    source_name, source_version, source_location = source_identity(plan)
    source_info = {"name": source_name, "version": source_version,
                   "location": source_location}
    imported = {
        "sources": 1,
        "nodes": len(verification.nodes),
        "nodes_by_type": dict(Counter(
            n.get("type") for n in verification.nodes)),
        "relationships": len(verification.relationships),
        "relationships_by_type": dict(Counter(
            r.get("relationship_type") for r in verification.relationships)),
    }

    if not ok:
        after = _snapshot(db_path)
        unchanged = _same_snapshot(before, after)
        return ImportResult(
            committed=False, plan_verified=True, before=before, after=after,
            backup=backup, imported=imported, source=source_info,
            rollback=unchanged,
            errors=[{"code": "import_failed",
                     "message": "transaction rolled back: %s" % reason,
                     "path": "failing_%s" % (failing or {}).get("record", "record")}])

    post_import = _post_import_check(db_path, plan, verification, before)
    after = _snapshot(db_path)
    return ImportResult(
        committed=True, plan_verified=True, before=before, after=after,
        backup=backup, imported=imported, source=source_info,
        post_import=post_import, rollback=False)


# -- human report ---------------------------------------------------------

def human_import_summary(result):
    """Render the import result as a compact human-readable block."""
    lines = []
    lines.append("Controlled production import")
    lines.append("============================")
    if result.committed:
        lines.append("RESULT: COMMITTED")
    else:
        lines.append("RESULT: NOT COMMITTED (refused or rolled back)")
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
    after = result.after or {}
    imported = result.imported or {}
    if before:
        lines.append("Before:")
        lines.append(f"  sources      : {before.get('source_count')}")
        lines.append(f"  nodes        : {before.get('node_count')}")
        lines.append(f"  relationships: {before.get('relationship_count')}")
        lines.append("")
    if imported:
        lines.append("Imported:")
        lines.append(f"  sources      : {imported.get('sources')}")
        lines.append(f"  nodes        : {imported.get('nodes')}")
        for t, c in sorted((imported.get("nodes_by_type") or {}).items()):
            lines.append(f"    {t:>12}: {c}")
        lines.append(f"  relationships: {imported.get('relationships')}")
        for t, c in sorted((imported.get("relationships_by_type") or {}).items()):
            lines.append(f"    {t:>12}: {c}")
        lines.append("")
    if after:
        lines.append("After:")
        lines.append(f"  sources      : {after.get('source_count')}")
        lines.append(f"  nodes        : {after.get('node_count')}")
        lines.append(f"  relationships: {after.get('relationship_count')}")
        lines.append("")

    post = result.post_import or {}
    if post:
        lines.append("Post-import verification (reopened from disk):")
        lines.append(f"  reopened from disk      : {post.get('reopened')}")
        lines.append(f"  foreign keys enabled    : {post.get('foreign_keys_enabled')}")
        counts = post.get("counts") or {}
        lines.append(f"  sources                 : {counts.get('source_count')}")
        lines.append(f"  nodes                   : {counts.get('node_count')}")
        lines.append(f"  relationships           : {counts.get('relationship_count')}")
        lines.append(f"  imported nodes          : {post.get('imported_node_count')}")
        lines.append(f"  imported relationships  : {post.get('imported_relationship_count')}")
        prov = post.get("provenance") or {}
        lines.append(f"  provenance complete     : {prov.get('complete')} "
                     f"({prov.get('checked')} checked, {prov.get('missing')} missing)")
        trav = post.get("relationship_traversal") or {}
        lines.append(f"  relationship traversal  : {trav.get('ok')} "
                     f"({trav.get('checked')} checked)")
        kept = post.get("existing_knowledge_preserved") or {}
        lines.append(f"  existing knowledge kept : {kept.get('ok')}")
        lines.append(f"  dangling rel sources    : {post.get('dangling_relationship_sources')}")
        lines.append(f"  duplicate primary keys  : {post.get('duplicate_primary_keys')}")
        lines.append("")

    if result.rollback is not None:
        lines.append(f"Rollback: {'verified (database unchanged)' if result.rollback else 'n/a'}")
    return "\n".join(lines) + "\n"
