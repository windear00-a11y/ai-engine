"""SQLite-backed knowledge persistence (KnowledgeRepository).

This module is the ONLY place that knows about SQLite. Higher layers
(``KnowledgeStore``) talk to a ``KnowledgeRepository`` abstraction so that a
future PostgreSQL repository can replace this one without touching the tools.

Design
------
* ``sources``  -> where a node came from (a JSON file import, etc.).
* ``nodes``    -> the knowledge nodes (id, type, name, description, extras).
* ``relationships`` -> first-class edges between nodes.

Constraints enforced by the schema / engine:
* Primary keys on every table.
* Foreign keys: ``nodes.source_id -> sources.id`` and
  ``relationships.source_node_id -> nodes.id`` (both ``ON DELETE CASCADE``).
  ``PRAGMA foreign_keys=ON`` is enabled per connection.
* ``nodes.id`` is UNIQUE -> duplicate node ids are rejected.
* ``relationships(source_node_id, relationship_type, target_node_id)`` is
  UNIQUE -> duplicate edges are rejected.
* ``target_node_id`` is intentionally NOT a foreign key: knowledge graphs
  legitimately contain forward/dangling references (e.g. a concept that
  references a technology not yet modeled). Invalid targets (empty/null) are
  rejected by ``add_relationship`` validation instead.

All queries are parameterized; no user input is ever string-interpolated into
SQL. Writes use explicit transactions so a failed import rolls back cleanly.
"""

import json
import os
import sqlite3
import time
from contextlib import contextmanager

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_KNOWLEDGE_DB = os.path.join(_ROOT, "database", "knowledge.db")

SCHEMA = """
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


class KnowledgeRepository:
    def __init__(self, db_path=":memory:", check_same_thread=True):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path, check_same_thread=check_same_thread)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")

    # -- lifecycle -------------------------------------------------------

    def initialize(self):
        with self.conn:
            self.conn.executescript(SCHEMA)
        return self

    def clear(self):
        with self.conn:
            self.conn.execute("DELETE FROM relationships")
            self.conn.execute("DELETE FROM nodes")
            self.conn.execute("DELETE FROM sources")
        return self

    def close(self):
        self.conn.close()

    @contextmanager
    def transaction(self):
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # -- internal helpers -------------------------------------------------

    def _node_dict(self, row, with_relationships=False):
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        node = dict(meta)
        node["id"] = row["id"]
        node["type"] = row["type"]
        node["name"] = row["name"]
        node["description"] = row["description"]
        node["source_id"] = row["source_id"]
        if row["source_id"] is not None:
            src = self.conn.execute(
                "SELECT name, version, location, imported_at FROM sources "
                "WHERE id = ?", (row["source_id"],)).fetchone()
            if src is not None:
                node["provenance"] = {
                    "source_id": row["source_id"],
                    "source_name": src["name"],
                    "source_version": src["version"],
                    "source_location": src["location"],
                    "imported_at": src["imported_at"],
                }
        if with_relationships:
            node["relationships"] = self.relationships_of(row["id"])
        return node

    # -- bulk hydration (avoids the per-node N+1 pattern) ------------------

    _SQL_PARAM_CHUNK = 500  # stay well under legacy SQLITE_MAX_VARIABLE_NUMBER

    def _chunks(self, items):
        items = list(items)
        for i in range(0, len(items), self._SQL_PARAM_CHUNK):
            yield items[i:i + self._SQL_PARAM_CHUNK]

    @staticmethod
    def _provenance_entry(source_id, src_row):
        return {
            "source_id": source_id,
            "source_name": src_row["name"],
            "source_version": src_row["version"],
            "source_location": src_row["location"],
            "imported_at": src_row["imported_at"],
        }

    def _source_map(self, source_ids):
        """One query (chunked) returning {source_id: row} for referenced ids."""
        ids = sorted({s for s in source_ids if s is not None})
        if not ids:
            return {}
        out = {}
        for chunk in self._chunks(ids):
            marks = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                "SELECT id, name, version, location, imported_at FROM sources "
                f"WHERE id IN ({marks})", chunk).fetchall()
            for r in rows:
                out[r["id"]] = r
        return out

    def _relationship_groups(self, node_ids):
        """One chunked query returning {node_id: [rel_dict, ...]}.

        Per-node relationship order matches ``relationships_of`` (rowid
        ascending within each source node), so output is byte-identical.
        Node ids are disjoint across chunks, so per-chunk ``ORDER BY``
        yields exactly the same grouping as a single query.
        """
        groups = {nid: [] for nid in node_ids}
        for chunk in self._chunks(node_ids):
            marks = ",".join("?" * len(chunk))
            rows = self.conn.execute(
                "SELECT source_node_id, relationship_type, target_node_id, "
                "label FROM relationships "
                f"WHERE source_node_id IN ({marks}) ORDER BY source_node_id, id",
                list(chunk)).fetchall()
            for r in rows:
                groups[r["source_node_id"]].append(
                    {"type": r["relationship_type"],
                     "target": r["target_node_id"],
                     "label": r["label"]})
        return groups

    def _hydrate_rows(self, rows, with_relationships=False):
        """Build node dicts for many rows using a constant number of queries.

        Produces exactly the same dict structure and key order as
        ``_node_dict`` (metadata extras first, then id/type/name/description/
        source_id, optional provenance, optional relationships).
        """
        prov_map = self._source_map([r["source_id"] for r in rows])
        rel_map = (self._relationship_groups([r["id"] for r in rows])
                   if with_relationships else None)
        nodes = []
        for row in rows:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            node = dict(meta)
            node["id"] = row["id"]
            node["type"] = row["type"]
            node["name"] = row["name"]
            node["description"] = row["description"]
            node["source_id"] = row["source_id"]
            sid = row["source_id"]
            if sid is not None and sid in prov_map:
                node["provenance"] = self._provenance_entry(sid, prov_map[sid])
            if with_relationships:
                node["relationships"] = rel_map.get(row["id"], [])
            nodes.append(node)
        return nodes

    def get_provenance(self, node_id):
        node = self.get_node(node_id)
        if node is None:
            return None
        return node.get("provenance")

    # -- sources ----------------------------------------------------------

    def add_source(self, name, version=None, location=None, metadata=None):
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO sources (name, version, location, imported_at, metadata) "
                "VALUES (?, ?, ?, ?, ?)",
                (name, version, location, time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 json.dumps(metadata) if metadata is not None else None),
            )
            return cur.lastrowid

    def get_source(self, source_id):
        row = self.conn.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return dict(row) if row else None

    # -- nodes ------------------------------------------------------------

    def add_node(self, node_id, node_type, name, description,
                 source_id=None, metadata=None):
        with self.conn:
            self.conn.execute(
                "INSERT INTO nodes (id, type, name, description, source_id, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (node_id, node_type, name, description, source_id,
                 json.dumps(metadata) if metadata is not None else None),
            )

    def update_node_metadata(self, node_id, metadata_merge):
        """Merge *metadata_merge* into an existing node's metadata JSON.

        Additive lifecycle extension (Phase 5): preserves all existing keys and
        merges the new ones. Returns True if the node existed and was updated,
        False if no such node exists (no write performed).
        """
        row = self.conn.execute(
            "SELECT metadata FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            return False
        meta = json.loads(row["metadata"]) if row["metadata"] else {}
        meta = dict(meta)
        meta.update(metadata_merge or {})
        with self.conn:
            self.conn.execute(
                "UPDATE nodes SET metadata = ? WHERE id = ?",
                (json.dumps(meta, sort_keys=True), node_id),
            )
        return True

    def get_node(self, node_id):
        row = self.conn.execute(
            "SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            return None
        return self._node_dict(row, with_relationships=True)

    def get_all_nodes(self):
        rows = self.conn.execute("SELECT * FROM nodes").fetchall()
        return self._hydrate_rows(rows, with_relationships=True)

    def _scan_scores(self, query):
        """Score every node against ``query`` using lightweight fields only.

        Returns ``[(score, row)]`` in table scan order (identical to the
        original search iteration order, so stable tie-breaking is preserved).
        No hydration happens here.
        """
        words = [w for w in query.lower().split() if w]
        rows = self.conn.execute(
            "SELECT id, type, name, description, source_id, metadata "
            "FROM nodes").fetchall()
        scored = []
        for row in rows:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            text = " ".join(str(v) for v in (
                row["id"], row["type"], row["name"], row["description"], meta
            )).lower()
            score = sum(1 for w in words if w in text)
            if score > 0:
                scored.append((score, row))
        return scored

    def search_nodes(self, query, limit=None):
        """Search with the exact original scoring/order semantics.

        Difference from the historical implementation: full hydration
        (provenance + relationships) now happens only for the nodes that are
        actually returned (after sorting and limiting), and it is batched into
        a constant number of queries instead of two queries per match.
        """
        scored = self._scan_scores(query)
        scored.sort(key=lambda x: x[0], reverse=True)
        if limit is not None:
            scored = scored[:limit]
        nodes = self._hydrate_rows([row for _, row in scored],
                                   with_relationships=True)
        for node, (score, _row) in zip(nodes, scored):
            node["_score"] = score
        return nodes

    def search_rankings(self, query):
        """Lightweight search rankings: [(score, node_id, node_type)].

        Same scan order and scoring as ``search_nodes``; intended for callers
        that filter/sort/limit before asking for hydration.
        """
        return [(score, row["id"], row["type"])
                for score, row in self._scan_scores(query)]

    def hydrate_nodes(self, node_ids, with_relationships=True):
        """Hydrate specific nodes by id, preserving the requested order."""
        ids = list(node_ids)
        if not ids:
            return []
        by_id = {}
        for chunk in self._chunks(ids):
            marks = ",".join("?" * len(chunk))
            for r in self.conn.execute(
                    f"SELECT * FROM nodes WHERE id IN ({marks})",
                    chunk).fetchall():
                by_id[r["id"]] = r
        ordered_rows = [by_id[nid] for nid in ids if nid in by_id]
        return self._hydrate_rows(ordered_rows,
                                  with_relationships=with_relationships)

    def count_nodes(self):
        return self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    # -- relationships ----------------------------------------------------

    def add_relationship(self, source_node_id, relationship_type,
                         target_node_id, label=None):
        if not isinstance(source_node_id, str) or not source_node_id:
            raise ValueError("source_node_id must be a non-empty string")
        if not isinstance(relationship_type, str) or not relationship_type:
            raise ValueError("relationship_type must be a non-empty string")
        if not isinstance(target_node_id, str) or not target_node_id:
            raise ValueError("target_node_id must be a non-empty string")
        if self.get_node(source_node_id) is None:
            raise ValueError(
                f"source node does not exist: {source_node_id!r}")
        with self.conn:
            self.conn.execute(
                "INSERT INTO relationships "
                "(source_node_id, relationship_type, target_node_id, label) "
                "VALUES (?, ?, ?, ?)",
                (source_node_id, relationship_type, target_node_id, label),
            )

    def relationships_of(self, node_id):
        rows = self.conn.execute(
            "SELECT relationship_type, target_node_id, label FROM relationships "
            "WHERE source_node_id = ?", (node_id,)).fetchall()
        return [{"type": r["relationship_type"],
                 "target": r["target_node_id"],
                 "label": r["label"]} for r in rows]

    def follow(self, node_id, rel_type=None):
        out = []
        for rel in self.relationships_of(node_id):
            if rel_type is not None and rel["type"] != rel_type:
                continue
            target = self.get_node(rel["target"])
            if target is not None:
                out.append((rel, target))
        return out

    def count_relationships(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM relationships").fetchone()[0]

    # -- bulk import (atomic per source file) -----------------------------

    def import_node(self, source_name, source_location, node_data,
                    source_version=None, source_metadata=None):
        """Insert one source + node + its relationships atomically.

        All inserts happen inside a single transaction so a failure (bad
        relationship, FK violation, duplicate) rolls back cleanly and leaves no
        partial state behind. Does NOT call the wrapped ``add_*`` helpers to
        avoid nested transactions.
        """
        nid = node_data["id"]
        ntype = node_data["type"]
        name = node_data.get("name")
        desc = node_data.get("description")
        extras = {
            k: v for k, v in node_data.items()
            if k not in ("id", "type", "name", "description",
                         "relationships", "_source")
        }
        for rel in node_data.get("relationships", []):
            if not isinstance(rel, dict):
                raise ValueError(f"relationship must be an object in node {nid}")
            if not rel.get("type") or not rel.get("target"):
                raise ValueError(
                    f"relationship missing type/target in node {nid}")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO sources (name, version, location, imported_at, metadata) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_name, source_version, source_location,
                 time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 json.dumps(source_metadata) if source_metadata is not None else None),
            )
            sid = cur.lastrowid
            self.conn.execute(
                "INSERT INTO nodes (id, type, name, description, source_id, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (nid, ntype, name, desc, sid,
                 json.dumps(extras) if extras else None),
            )
            for rel in node_data.get("relationships", []):
                self.conn.execute(
                    "INSERT INTO relationships "
                    "(source_node_id, relationship_type, target_node_id, label) "
                    "VALUES (?, ?, ?, ?)",
                    (nid, rel["type"], rel["target"], rel.get("label")),
                )

    def import_source(self, source_name, source_location, nodes,
                      source_version=None, source_metadata=None):
        """Insert one source + all of its nodes + relationships atomically.

        This is the bulk counterpart of :meth:`import_node`: a single source
        contributes many nodes that all share one ``source_id``. Everything runs
        inside one transaction, so if any insert fails (bad relationship, FK
        violation, duplicate id) the whole source is rolled back and nothing
        partial remains in the database.

        ``nodes`` are *normalized* node dicts as produced by
        ``ingestion.validator.validate_source`` (each has ``id``, ``type``,
        ``name``, ``description``, ``relationships`` and an ``_extras`` map of
        additional metadata).
        """
        if not isinstance(nodes, list) or not nodes:
            raise ValueError("nodes must be a non-empty list")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO sources (name, version, location, imported_at, metadata) "
                "VALUES (?, ?, ?, ?, ?)",
                (source_name, source_version, source_location,
                 time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                 json.dumps(source_metadata) if source_metadata is not None
                 else None),
            )
            sid = cur.lastrowid
            for node in nodes:
                nid = node["id"]
                ntype = node["type"]
                name = node.get("name")
                desc = node.get("description")
                extras = node.get("_extras", {})
                self.conn.execute(
                    "INSERT INTO nodes (id, type, name, description, source_id, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (nid, ntype, name, desc, sid,
                     json.dumps(extras) if extras else None),
                )
                for rel in node.get("relationships", []):
                    if not rel.get("type") or not rel.get("target"):
                        raise ValueError(
                            f"relationship missing type/target in node {nid}")
                    self.conn.execute(
                        "INSERT INTO relationships "
                        "(source_node_id, relationship_type, target_node_id, label) "
                        "VALUES (?, ?, ?, ?)",
                        (nid, rel["type"], rel["target"], rel.get("label")),
                    )
        return sid
