"""Deterministic relationship backfill proposals (dry-run only).

The corpus imported from CPython documentation carries almost no graph
structure: thousands of ``example`` and ``dependency`` nodes exist with
zero outgoing edges. This module derives *deterministic* relationship
proposals from evidence that is already recorded inside each node -- it
never guesses, never invents target nodes, and NEVER writes anything.

This module is a pure REPORTER:

* opens the database with SQLite ``mode=ro`` (the file cannot be modified),
* reads node metadata only,
* emits a sorted, timestamp-free, byte-stable JSON/text report,
* classifies every candidate as ``proposed``, ``duplicate`` (edge already
  exists), ``ambiguous`` (target resolves to more than one node) or
  ``unmatched`` (rule preconditions not met).

Proposed rules
--------------
E1 ``example_library_doc``
    An ``example`` node whose evidence references ALL point at the single
    document ``library/<mod>.rst`` is proposed as ``example_of`` the
    unique ``technology`` node named ``<mod>`` (case-insensitive,
    trimmed). Examples citing tutorials, whatsnews, howtos etc. have no
    deterministic parent and are left unmatched on purpose.

D1 ``dependency_import_name``
    A ``dependency`` node whose name parses as a Python import statement
    (``import a[, b...]`` / ``from m import x``) is proposed as
    ``depends_on`` each top-level module for which exactly one
    ``technology`` node exists. Non-import names (e.g. prose) are
    unmatched.

Every proposal carries ``label = "inferred:<rule_id>"`` so that a future
apply step can be audited and told apart from authored/source
relationships. See SCHEMA.md: ``label`` is free text and NOT part of the
relationships uniqueness key, therefore inferred labels cannot collide
with authored data and repeated dry-runs stay stable.

CLI::

    python -m knowledge_compiler.backfill [--db PATH] [--json] [--rule ID]

Usage against the production database is safe: the connection is opened
read-only at the SQLite level.
"""

import argparse
import json
import os
import re
import sqlite3
import sys

try:
    from retrieval.repository import DEFAULT_KNOWLEDGE_DB
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from retrieval.repository import DEFAULT_KNOWLEDGE_DB

LIB_DOC = re.compile(r"^library/([\w.]+)\.rst$")
IMPORT_NAME = re.compile(
    r"^\s*(?:from\s+([A-Za-z_][\w.]*+)\s+import\b"
    r"|import\s+(?:([\w.+]+)(?:\s*,\s*([\w.+]+))*))")

RULE_E1 = "example_library_doc"
RULE_D1 = "dependency_import_name"
REL_EXAMPLE_OF = "example_of"
REL_DEPENDS_ON = "depends_on"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _rows(con, sql, params=()):
    return con.execute(sql, params).fetchall()


def _tech_index(con):
    """name(lower,trimmed) -> [technology node ids], sorted & de-duplicated."""
    idx = {}
    for nid, name in _rows(
            con, "SELECT id, name FROM nodes WHERE type='technology' "
                 "ORDER BY id"):
        if name:
            idx.setdefault(name.strip().lower(), []).append(nid)
    return idx


def _unique_target(idx, key):
    """Return the single target id for ``key`` or None (0 hits -> None kept
    distinct by caller via 'unmatched'; >1 hits -> ambiguous)."""
    return idx.get(key) or []


def _classify(targets, valid, ambiguous, unmatched, payload):
    if len(targets) == 1:
        valid.append(payload | {"target_node_id": targets[0]})
    elif len(targets) > 1:
        ambiguous.append(payload | {"candidate_targets": sorted(targets)})
    else:
        unmatched.append(payload)


def _top_modules(match):
    """Top-level module names from an IMPORT_NAME match (order-preserving)."""
    mods = []
    if match.group(1):
        mods = [match.group(1)]
    else:
        mods = [g for g in match.groups()[1:] if g]
    out = []
    for mod in mods:
        top = mod.split(".")[0].strip()
        if top and top not in out:
            out.append(top)
    return out


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------

def rule_e1_example_of(con, existing_edges):
    """library/X.rst examples -> example_of technology X."""
    idx = _tech_index(con)
    valid, ambiguous, unmatched, duplicates = [], [], [], []
    total = 0
    for nid, meta in _rows(
            con, "SELECT id, metadata FROM nodes WHERE type='example' "
                 "ORDER BY id"):
        total += 1
        try:
            m = json.loads(meta) if meta else {}
        except (TypeError, ValueError):
            m = {}
        refs = m.get("evidence_references") or []
        docs = sorted({(r or {}).get("document") or "" for r in refs})
        payload = {"source_node_id": nid, "rule": RULE_E1,
                   "relationship_type": REL_EXAMPLE_OF,
                   "confidence": "deterministic"}
        if len(docs) != 1:
            payload["reason"] = (
                "evidence cites %d distinct documents" % len(docs))
            unmatched.append(payload)
            continue
        lm = LIB_DOC.match(docs[0])
        if not lm:
            payload["reason"] = "document %r is not library/<mod>.rst" % docs[0]
            unmatched.append(payload)
            continue
        payload["evidence"] = {"document": docs[0],
                               "module": lm.group(1)}
        targets = _unique_target(idx, lm.group(1).strip().lower())
        if not targets:
            payload["reason"] = "no technology node named %r" % lm.group(1)
            unmatched.append(payload)
            continue
        _classify(targets, valid, ambiguous, unmatched, payload)

    proposed = []
    for p in valid:
        key = (p["source_node_id"], REL_EXAMPLE_OF, p["target_node_id"])
        if key in existing_edges:
            duplicates.append(p)
        else:
            proposed.append(p | {"label": "inferred:" + RULE_E1})
    return {"total_candidates": total, "proposed": proposed,
            "duplicates": duplicates, "ambiguous": ambiguous,
            "unmatched": unmatched}


def rule_d1_depends_on(con, existing_edges):
    """import-shaped dependency names -> depends_on technology."""
    idx = _tech_index(con)
    valid, ambiguous, unmatched, duplicates = [], [], [], []
    total = 0
    for nid, name in _rows(
            con, "SELECT id, name FROM nodes WHERE type='dependency' "
                 "ORDER BY id"):
        total += 1
        payload_base = {"source_node_id": nid, "rule": RULE_D1,
                        "relationship_type": REL_DEPENDS_ON,
                        "confidence": "deterministic", "evidence": {}}
        match = IMPORT_NAME.match(name or "")
        if not match:
            p = payload_base | {"reason": "name %r is not an import "
                                          "statement" % (name,)}
            unmatched.append(p)
            continue
        mods = _top_modules(match)
        payload_base["evidence"] = {"parsed_modules": mods, "name": name}
        resolved_any = False
        local_valid, local_ambig = [], []
        for mod in mods:
            targets = _unique_target(idx, mod.lower())
            if len(targets) == 1:
                local_valid.append((mod, targets[0]))
                resolved_any = True
            elif len(targets) > 1:
                local_ambig.append(mod)
                resolved_any = True
            else:
                p = payload_base | {
                    "reason": "no technology node named %r" % mod,
                    "evidence": {"parsed_modules": mods, "module": mod}}
                unmatched.append(p)
        if local_ambig:
            ambiguous.append(payload_base | {
                "ambiguous_modules": sorted(local_ambig)})
            continue
        if not resolved_any:
            continue
        for mod, tid in local_valid:
            valid.append(payload_base | {
                "target_node_id": tid,
                "evidence": {"parsed_modules": mods, "module": mod}})

    proposed = []
    for p in valid:
        key = (p["source_node_id"], REL_DEPENDS_ON, p["target_node_id"])
        if key in existing_edges:
            duplicates.append(p)
        else:
            proposed.append(p | {"label": "inferred:" + RULE_D1})
    return {"total_candidates": total, "proposed": proposed,
            "duplicates": duplicates, "ambiguous": ambiguous,
            "unmatched": unmatched}


RULES = {RULE_E1: rule_e1_example_of, RULE_D1: rule_d1_depends_on}


# --------------------------------------------------------------------------
# dangling analysis + projection
# --------------------------------------------------------------------------

def dangling_analysis(con):
    rows = _rows(
        con,
        "SELECT r.id, r.source_node_id, r.relationship_type, "
        "       r.target_node_id, sn.name AS src_name "
        "FROM relationships r JOIN nodes sn ON sn.id = r.source_node_id "
        "WHERE NOT EXISTS (SELECT 1 FROM nodes n WHERE n.id = r.target_node_id) "
        "ORDER BY r.id")
    out = []
    for rid, sid, rt, tid, sname in rows:
        matches = [x[0] for x in _rows(
            con, "SELECT id FROM nodes WHERE LOWER(TRIM(name))=LOWER(?) "
                 "ORDER BY id", (tid,))]
        out.append({
            "relationship_id": rid,
            "source_node_id": sid,
            "source_name": sname,
            "relationship_type": rt,
            "target_node_id": tid,
            "resolvable": bool(matches),
            "resolution": ("exact-name-match: %s" % matches
                           if matches else
                           "no node with this id or name; refusing to invent"),
        })
    return out


def project_stats(con, extra_edges):
    """Hypothetical connectivity after adding ``extra_edges`` (no writes)."""
    total_nodes = con.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
    total_rels = con.execute("SELECT COUNT(*) FROM relationships").fetchone()[0]
    node_ids = {r[0] for r in con.execute("SELECT id FROM nodes")}
    connected_now = set()
    for sid, tid in con.execute(
            "SELECT source_node_id, target_node_id FROM relationships"):
        connected_now.add(sid)
        if tid in node_ids:
            connected_now.add(tid)
    connected = set(connected_now)
    for p in extra_edges:
        connected.add(p["source_node_id"])
        connected.add(p["target_node_id"])
    isolated_current = total_nodes - len(connected_now)
    isolated = total_nodes - len(connected)
    pct = round(100.0 * len(connected) / total_nodes, 2) if total_nodes else 0.0
    iso_pct = round(100.0 * isolated / total_nodes, 2) if total_nodes else 0.0
    return {"nodes": total_nodes,
            "relationships_current": total_rels,
            "relationships_projected": total_rels + len(extra_edges),
            "isolated_current": isolated_current,
            "isolated_projected": isolated,
            "connectivity_projected_pct": pct,
            "isolated_projected_pct": iso_pct}


# --------------------------------------------------------------------------
# report assembly
# --------------------------------------------------------------------------

def build_report(db_path, rules=None):
    rules = list(RULES) if rules is None else rules
    uri = "file:{}?mode=ro".format(os.path.abspath(db_path))
    con = sqlite3.connect(uri, uri=True)
    try:
        existing = {
            (r[0], r[1], r[2]) for r in con.execute(
                "SELECT source_node_id, relationship_type, target_node_id "
                "FROM relationships")}
        results = {}
        for rule_id in sorted(rules):
            fn = RULES.get(rule_id)
            if fn is None:
                raise ValueError("unknown rule: %r" % rule_id)
            results[rule_id] = fn(con, existing)
        proposed_all = []
        for rule_id in sorted(results):
            proposed_all.extend(results[rule_id]["proposed"])
        proposed_all.sort(key=lambda p: (p["rule"], p["source_node_id"],
                                         p["target_node_id"]))
        report = {
            "db_path": os.path.abspath(db_path),
            "mode": "dry-run (read-only)",
            "rules": {},
            "proposed_edges_total": len(proposed_all),
            "duplicates_total": sum(
                len(r["duplicates"]) for r in results.values()),
            "ambiguous_total": sum(
                len(r["ambiguous"]) for r in results.values()),
            "unmatched_total": sum(
                len(r["unmatched"]) for r in results.values()),
            "dangling_existing": dangling_analysis(con),
            "projected_stats": project_stats(con, proposed_all),
        }
        for rule_id in sorted(results):
            r = results[rule_id]
            report["rules"][rule_id] = {
                "relationship_type": (
                    REL_EXAMPLE_OF if rule_id == RULE_E1 else REL_DEPENDS_ON),
                "total_candidates": r["total_candidates"],
                "valid_deterministic_matches": len(r["proposed"]) + len(
                    r["duplicates"]),
                "proposed_new_edges": len(r["proposed"]),
                "duplicate_edges_already_present": len(r["duplicates"]),
                "ambiguous_matches": len(r["ambiguous"]),
                "unmatched_nodes": len(r["unmatched"]),
                "invalid_targets": 0,
                "proposals": [
                    {"relationship_type": p["relationship_type"],
                     "source_node_id": p["source_node_id"],
                     "target_node_id": p["target_node_id"],
                     "rule": p["rule"],
                     "confidence": p["confidence"],
                     "evidence": p.get("evidence"),
                     "label": p["label"]}
                    for p in sorted(
                        r["proposed"],
                        key=lambda x: (x["source_node_id"],
                                       x["target_node_id"]))],
                "ambiguous_cases": r["ambiguous"],
                "unmatched_cases": r["unmatched"],
                "duplicate_cases": r["duplicates"],
            }
        return report
    finally:
        con.close()


def render_text(report):
    lines = ["Relationship Backfill Dry-Run", "=" * 60]
    lines.append("db      : %s" % report["db_path"])
    lines.append("mode    : %s" % report["mode"])
    ps = report["projected_stats"]
    lines.append("")
    for rid in sorted(report["rules"]):
        r = report["rules"][rid]
        lines.append("rule %-22s (%s)" % (rid, r["relationship_type"]))
        lines.append("  candidates          : %d" % r["total_candidates"])
        lines.append("  proposed new edges  : %d" % r["proposed_new_edges"])
        lines.append("  duplicates (exist)  : %d"
                     % r["duplicate_edges_already_present"])
        lines.append("  ambiguous           : %d" % r["ambiguous_matches"])
        lines.append("  unmatched           : %d" % r["unmatched_nodes"])
        lines.append("")
    lines.append("proposed edges total : %d" % report["proposed_edges_total"])
    lines.append("existing dangling    : %d (all reported, none invented)"
                 % len(report["dangling_existing"]))
    for d in report["dangling_existing"]:
        lines.append("  #%d %s -[%s]-> %s : %s" % (
            d["relationship_id"], d["source_node_id"],
            d["relationship_type"], d["target_node_id"], d["resolution"]))
    lines.append("")
    lines.append("projected graph")
    lines.append("  relationships : %d -> %d" % (
        ps["relationships_current"], ps["relationships_projected"]))
    lines.append("  isolated      : %d -> %d" % (
        ps["isolated_current"], ps["isolated_projected"]))
    lines.append("  connectivity  : -> %.2f%%"
                 % ps["connectivity_projected_pct"])
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m knowledge_compiler.backfill",
        description="Deterministic relationship-backfill dry-run "
                    "(strictly read-only).")
    parser.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB,
                        help="database path (opened read-only)")
    parser.add_argument("--json", action="store_true",
                        help="emit full JSON report")
    parser.add_argument("--rule", action="append", default=None,
                        metavar="ID",
                        help="restrict to a rule (repeatable); default: all")
    args = parser.parse_args(argv)

    if not os.path.exists(args.db):
        print("error: database not found: %s" % args.db, file=sys.stderr)
        return 2
    try:
        report = build_report(args.db, rules=args.rule)
    except (sqlite3.DatabaseError, ValueError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True)
          if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
