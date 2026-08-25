"""Read-only knowledge graph health reporting.

This module answers one question: *what shape is the knowledge graph in
right now?* It is a diagnostic companion to :mod:`retrieval.repository`
and is used during migration/backfill work to measure impact before and
after a change.

Guarantees
----------
* **Read-only.** The database is opened with SQLite's ``mode=ro`` URI so
  the underlying file physically cannot be modified -- not by this
  module, not by accident, not under any code path.
* **Deterministic.** All breakdowns are sorted; running the report twice
  against an unchanged database produces byte-identical output.
* **No schema requirements** beyond the three core tables (``sources``,
  ``nodes``, ``relationships``).

Reported metrics
----------------
- totals for nodes, relationships, sources
- isolated nodes (no incoming or outgoing edges) and percentage
- dangling targets (edges whose target node does not exist)
- relationships grouped by type
- nodes grouped by type
- connectivity percentage (non-isolated / total)
- per-source node counts

CLI::

    python3 -m retrieval.graph_report [--db PATH] [--json]
"""

import argparse
import json
import os
import sqlite3
import sys

try:
    from retrieval.repository import DEFAULT_KNOWLEDGE_DB
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from retrieval.repository import DEFAULT_KNOWLEDGE_DB


def _pct(part, whole):
    return round(100.0 * part / whole, 2) if whole else 0.0


def _report(con):
    cur = con.cursor()

    def one(q):
        return cur.execute(q).fetchone()[0]

    total_nodes = one("SELECT COUNT(*) FROM nodes")
    total_rels = one("SELECT COUNT(*) FROM relationships")
    isolated = one(
        "SELECT COUNT(*) FROM nodes n "
        "WHERE NOT EXISTS (SELECT 1 FROM relationships r "
        "WHERE r.source_node_id = n.id OR r.target_node_id = n.id)")

    dangling_details = [
        {"source_node_id": r[0], "type": r[1], "target_node_id": r[2]}
        for r in cur.execute(
            "SELECT r.source_node_id, r.relationship_type, r.target_node_id "
            "FROM relationships r "
            "WHERE NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = r.target_node_id) "
            "ORDER BY r.source_node_id, r.relationship_type, r.target_node_id")
    ]

    return {
        "total_nodes": total_nodes,
        "total_relationships": total_rels,
        "total_sources": one("SELECT COUNT(*) FROM sources"),
        "isolated_nodes": isolated,
        "isolated_pct": _pct(isolated, total_nodes),
        "connectivity_pct": _pct(total_nodes - isolated, total_nodes),
        "dangling_targets": len(dangling_details),
        "dangling_target_edges": dangling_details,
        "relationships_by_type": {
            row[0]: row[1] for row in cur.execute(
                "SELECT relationship_type, COUNT(*) FROM relationships "
                "GROUP BY relationship_type ORDER BY relationship_type")
        },
        "nodes_by_type": {
            row[0]: row[1] for row in cur.execute(
                "SELECT type, COUNT(*) FROM nodes GROUP BY type ORDER BY type")
        },
        "nodes_per_source": [
            {"source_id": row[0], "name": row[1], "node_count": row[2]}
            for row in cur.execute(
                "SELECT s.id, s.name, COUNT(n.id) FROM sources s "
                "LEFT JOIN nodes n ON n.source_id = s.id "
                "GROUP BY s.id, s.name ORDER BY s.id")
        ],
    }


def build_report(db_path):
    """Build the report dict for the database at ``db_path`` (read-only)."""
    uri = "file:{}?mode=ro".format(os.path.abspath(db_path))
    con = sqlite3.connect(uri, uri=True)
    try:
        return _report(con)
    finally:
        con.close()


def render_text(report):
    """Render a report dict as a stable, human-readable table block."""
    lines = []
    lines.append("Knowledge Graph Report")
    lines.append("-" * 48)
    lines.append("nodes:            {}".format(report["total_nodes"]))
    lines.append("relationships:    {}".format(report["total_relationships"]))
    lines.append("sources:          {}".format(report["total_sources"]))
    lines.append("isolated nodes:   {} ({}%)".format(
        report["isolated_nodes"], report["isolated_pct"]))
    lines.append("connectivity:     {}%".format(report["connectivity_pct"]))
    lines.append("dangling targets: {}".format(report["dangling_targets"]))
    lines.append("")
    lines.append("nodes by type:")
    for k in sorted(report["nodes_by_type"]):
        lines.append("  {:<14}{}".format(k, report["nodes_by_type"][k]))
    lines.append("")
    lines.append("relationships by type:")
    for k in sorted(report["relationships_by_type"]):
        lines.append("  {:<14}{}".format(k, report["relationships_by_type"][k]))
    if report["dangling_target_edges"]:
        lines.append("")
        lines.append("dangling edges:")
        for e in report["dangling_target_edges"]:
            lines.append("  {} -{}-> {}".format(
                e["source_node_id"], e["type"], e["target_node_id"]))
    lines.append("")
    lines.append("sources:")
    for s in report["nodes_per_source"]:
        lines.append("  #{} {:<40} {} nodes".format(
            s["source_id"], s["name"], s["node_count"]))
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python3 -m retrieval.graph_report",
        description="Read-only knowledge graph health report.")
    parser.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB,
                        help="path to the SQLite database (default: %(default)s)")
    parser.add_argument("--json", action="store_true",
                        help="emit machine-readable JSON instead of text")
    args = parser.parse_args(argv)

    if not os.path.exists(args.db):
        print("error: database not found: {}".format(args.db), file=sys.stderr)
        return 2
    try:
        report = build_report(args.db)
    except sqlite3.DatabaseError as exc:
        print("error: cannot open database read-only: {}".format(exc),
              file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
