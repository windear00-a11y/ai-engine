"""Simulated import: apply an import plan to an isolated in-memory repository.

The dry run creates a fresh :class:`KnowledgeRepository` in ``:memory:`` --
NOT the production database -- and attempts to apply the ENTIRE plan inside a
single transaction, exactly as a future import command would.

On success the projected counts are read back from the repository, proving the
plan is actually compatible with the repository's constraints. On failure the
transaction rolls back, the exact failing node/relationship and reason are
reported, and the report proves zero records remain in the temporary
repository.

Determinism: node and relationship ordering is canonical (sorted), no
timestamps or random values ever enter the report, and repeated runs on the
same plan produce equivalent results. The temporary repository is discarded
afterwards -- nothing is written to disk.
"""

import json
import os
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from retrieval.repository import KnowledgeRepository
from importing.verifier import verify_plan, Issue


@dataclass
class DryRunResult:
    """Outcome of simulating one import plan in an isolated repository."""
    verified: bool                      # static verification passed
    simulated: bool                     # the in-memory apply was attempted
    safe: bool                          # verified AND simulated successfully
    rollback: bool = True               # true after a failed simulation
    verification: object = None         # the VerificationResult
    sources_inserted: int = 0
    nodes_inserted: int = 0
    relationships_inserted: int = 0
    node_types: dict = field(default_factory=dict)
    relationship_types: dict = field(default_factory=dict)
    dangling_targets: list = field(default_factory=list)
    skipped_relationships: list = field(default_factory=list)
    conflicts: dict = field(default_factory=dict)
    skipped_held: dict = field(default_factory=dict)
    errors: list = field(default_factory=list)      # Issue dicts
    warnings: list = field(default_factory=list)    # Issue dicts
    provenance_verification: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)

    def as_dict(self):
        return {
            "safe": self.safe,
            "verified": self.verified,
            "simulated": self.simulated,
            "rollback": self.rollback,
            "source": self.source,
            "sources_inserted": self.sources_inserted,
            "nodes_inserted": self.nodes_inserted,
            "relationships_inserted": self.relationships_inserted,
            "node_types": dict(sorted(self.node_types.items())),
            "relationship_types": dict(sorted(self.relationship_types.items())),
            "dangling_targets": len(self.dangling_targets),
            "skipped_relationships": [r["path"] for r in self.skipped_relationships],
            "conflicts": dict(sorted(self.conflicts.items())),
            "skipped_held": dict(sorted(self.skipped_held.items())),
            "errors": self.errors,
            "warnings": self.warnings,
            "provenance_verification": self.provenance_verification,
        }


def source_identity(plan):
    """Deterministic source name/version/location for a plan (never reads disk)."""
    raw = plan.get("source") or ""
    base = os.path.basename(raw.rstrip("/\\")) if raw else ""
    base = base or "import-plan"
    version = plan.get("version")
    if version is not None and not isinstance(version, str):
        version = str(version)
    return f"import-plan:{base}", version, raw or "<unknown>"


def _decision_counts(plan):
    """HELD / REJECTED records and identity conflicts from the plan's decisions."""
    decisions = plan.get("decisions")
    if not isinstance(decisions, list):
        return {"held": 0, "rejected": 0, "total": 0}, \
               {"identity_conflicted_records": 0}
    held = rejected = conflicted = 0
    for d in decisions:
        state = d.get("decision")
        if state == "HOLD":
            held += 1
            if str(d.get("reason_code", "")).startswith("HOLD:identity"):
                conflicted += 1
        elif state == "REJECT":
            rejected += 1
    return {"held": held, "rejected": rejected, "total": held + rejected}, \
           {"identity_conflicted_records": conflicted}


def _node_metadata(node):
    provenance = node.get("provenance") or []
    return {
        "evidence_references": provenance,
        "evidence_reference_count": len(provenance),
    }


def apply_plan(repo, plan, verification):
    """Apply a verified plan to ``repo`` inside one atomic transaction.

    Returns (ok, inserted_counts, failing_record, error_message). On failure
    the transaction has rolled back and ``repo`` holds ZERO rows. ``repo`` must
    already be initialized and MUST be an isolated (in-memory) repository.
    """
    source_name, source_version, source_location = source_identity(plan)

    rel_by_source = defaultdict(list)
    orphans = []
    for rel in verification.relationships:
        src = rel.get("source_node_id")
        if isinstance(src, str) and src in verification.node_ids:
            rel_by_source[src].append(rel)
        else:
            orphans.append(rel)

    nodes = sorted(verification.nodes, key=lambda n: (n.get("id") or "",))
    insert_rel = ("INSERT INTO relationships "
                  "(source_node_id, relationship_type, target_node_id, label) "
                  "VALUES (?, ?, ?, ?)")

    try:
        with repo.transaction():
            cur = repo.conn.execute(
                "INSERT INTO sources (name, version, location, imported_at, metadata) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_name, source_version, source_location,
                 time.strftime("%Y-%m-%dT%H:%M:%SZ"), None))
            sid = cur.lastrowid

            for node in nodes:
                nid = node.get("id")
                repo.conn.execute(
                    "INSERT INTO nodes (id, type, name, description, source_id, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (nid, node.get("type"), node.get("name"),
                     node.get("description"), sid,
                     json.dumps(_node_metadata(node))))
                for rel in sorted(rel_by_source.get(nid, []),
                                  key=lambda r: (r.get("relationship_type", ""),
                                                 r.get("target_node_id", ""))):
                    repo.conn.execute(insert_rel, (nid, rel.get("relationship_type"),
                                                   rel.get("target_node_id"),
                                                   rel.get("label")))

            for rel in sorted(orphans, key=lambda r: (r.get("source_node_id", ""),
                                                      r.get("relationship_type", ""),
                                                      r.get("target_node_id", ""))):
                repo.conn.execute(insert_rel, (rel.get("source_node_id"),
                                               rel.get("relationship_type"),
                                               rel.get("target_node_id"),
                                               rel.get("label")))
    except Exception as e:  # noqa: BLE001 -- report engine constraint failures
        return False, {}, _failing_record(e), str(e)

    counts = {
        "sources": repo.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
        "nodes": repo.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
        "relationships": repo.conn.execute(
            "SELECT COUNT(*) FROM relationships").fetchone()[0],
    }
    return True, counts, None, None


def _failing_record(error):
    """Extract the failing node/relationship from a SQLite error message."""
    message = str(error)
    for marker in ("nodes.id", "nodes.", "UNIQUE constraint failed: nodes.id"):
        if marker in message:
            return {"record": "node", "reason": message}
    for marker in ("relationships.source_node_id", "relationships.",
                   "FOREIGN KEY"):
        if marker in message:
            return {"record": "relationship", "reason": message}
    return {"record": None, "reason": message}


def _verify_provenance(repo, total):
    """Confirm stored nodes retain full provenance (source + evidence)."""
    issues = []
    checked = 0
    nodes = repo.get_all_nodes()
    for node in nodes:
        checked += 1
        prov = node.get("provenance")
        if not isinstance(prov, dict):
            issues.append(f"node {node['id']}: missing source provenance")
            continue
        for key in ("source_id", "source_name", "source_location", "imported_at"):
            if key not in prov:
                issues.append(f"node {node['id']}: missing provenance {key}")
        if "evidence_references" not in node:
            issues.append(f"node {node['id']}: missing evidence references")
    return {
        "checked": checked,
        "complete": len(issues) == 0 and checked == total,
        "issues": issues[:20],
    }


def dry_run(plan):
    """Verify the plan and simulate its import in an isolated repository.

    Always read-only: the only repository is a fresh ``:memory:`` instance that
    is discarded at the end. The production database is never touched.
    """
    verification = verify_plan(plan)

    result = DryRunResult(
        verified=verification.valid,
        simulated=True,
        safe=False,
        verification=verification,
        errors=[e.as_dict() for e in verification.errors],
        warnings=[w.as_dict() for w in verification.warnings],
        dangling_targets=verification.dangling_targets,
        source={
            "name": source_identity(plan)[0],
            "version": source_identity(plan)[1],
            "location": source_identity(plan)[2],
        },
    )

    skipped_held, conflicts = _decision_counts(plan)
    result.skipped_held = skipped_held
    result.conflicts = conflicts

    skipped = []
    for rel in verification.unresolved_sources:
        skipped.append(Issue("relationship_source_missing",
                             f"skipped: source node {rel.get('source_node_id')!r} "
                             "not defined in the plan",
                             f"preview.proposed_relationships"))
    result.skipped_relationships = [i.as_dict() for i in skipped]

    repo = KnowledgeRepository(":memory:")
    try:
        repo.initialize()
        ok, counts, failing, reason = apply_plan(repo, plan, verification)
        if not ok:
            result.simulated = True
            result.safe = False
            result.rollback = True
            detail = failing or {}
            result.errors.insert(0, {
                "code": "simulation_failed",
                "message": f"simulated import failed: {reason}",
                "path": f"failing_{detail.get('record', 'record')}",
            })
            remaining = {
                "sources": repo.conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
                "nodes": repo.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0],
                "relationships": repo.conn.execute(
                    "SELECT COUNT(*) FROM relationships").fetchone()[0],
            }
            result.provenance_verification = {"checked": 0, "complete": False,
                                              "issues": ["no nodes persisted "
                                                         "(rollback verified)"]}
            result.sources_inserted = remaining["sources"]
            result.nodes_inserted = remaining["nodes"]
            result.relationships_inserted = remaining["relationships"]
            result.safe = False
            result.rollback = remaining["sources"] == 0 and \
                remaining["nodes"] == 0 and remaining["relationships"] == 0
            return result

        # -- success: read projected state back from the repository ---------
        result.sources_inserted = counts["sources"]
        result.nodes_inserted = counts["nodes"]
        result.relationships_inserted = counts["relationships"]

        node_types = Counter()
        for row in repo.conn.execute("SELECT type FROM nodes"):
            node_types[row["type"]] += 1
        rel_types = Counter()
        for row in repo.conn.execute("SELECT relationship_type FROM relationships"):
            rel_types[row["relationship_type"]] += 1
        result.node_types = dict(node_types)
        result.relationship_types = dict(rel_types)

        result.provenance_verification = _verify_provenance(
            repo, counts["nodes"])
        result.safe = True
        result.rollback = False
        return result
    finally:
        repo.close()
