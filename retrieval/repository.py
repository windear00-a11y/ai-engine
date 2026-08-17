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
    def __init__(self, db_path=":memory:"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
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

    def get_node(self, node_id):
        row = self.conn.execute(
            "SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            return None
        return self._node_dict(row, with_relationships=True)

    def get_all_nodes(self):
        rows = self.conn.execute("SELECT * FROM nodes").fetchall()
        return [self._node_dict(r, with_relationships=True) for r in rows]

    def search_nodes(self, query, limit=None):
        words = [w for w in query.lower().split() if w]
        rows = self.conn.execute(
            "SELECT id, type, name, description, source_id, metadata "
            "FROM nodes").fetchall()
        results = []
        for row in rows:
            meta = json.loads(row["metadata"]) if row["metadata"] else {}
            text = " ".join(str(v) for v in (
                row["id"], row["type"], row["name"], row["description"], meta
            )).lower()
            score = sum(1 for w in words if w in text)
            if score > 0:
                node = self._node_dict(row, with_relationships=True)
                node["_score"] = score
                results.append((score, node))
        results.sort(key=lambda x: x[0], reverse=True)
        if limit is not None:
            results = results[:limit]
        return [n for _, n in results]

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
