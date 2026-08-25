"""Public Knowledge API (deterministic, read-only).

The Knowledge API is the stable programmatic interface of the knowledge
engine. It wraps :class:`KnowledgeStore` / :class:`KnowledgeRepository` and
exposes only approved operations:

* ``search``    -- text query with optional node-type filter and limit
* ``get``       -- one node by id (type, metadata, relationships, provenance)
* ``related``   -- deterministic neighbours via existing relationships
* ``follow``    -- existing relationship traversal (no invented semantics)
* ``inspect``   -- database facts (counts by type)
* ``provenance``-- source/provenance for one node

Design rules:

* READ-ONLY: no method ever writes to the repository. Search, get, related,
  follow, inspect and provenance only read.
* DETERMINISTIC: every list result is ordered by a stable key -- no reliance
  on unordered SQLite iteration.
* NO RAW SQLITE: callers never receive a connection; results are plain,
  JSON-serializable dicts/lists.
* PRESERVE EXISTING SEMANTICS: search scoring follows the repository's
  ``search_nodes`` behaviour; follow matches the store/repository traversal.
"""

import os

from api.errors import (
    KnowledgeArgumentError,
    NodeNotFoundError,
    RelationshipTypeError,
)
from retrieval.knowledge import VALID_TYPES, RELATIONSHIP_KINDS, KnowledgeStore
from retrieval.repository import KnowledgeRepository, DEFAULT_KNOWLEDGE_DB


def _is_positive_int(value):
    return isinstance(value, int) and value > 0


class KnowledgeAPI:
    """Deterministic, read-only facade over the knowledge database.

    Defaults to the canonical ``database/knowledge.db``. Tests pass an explicit
    ``db_path`` (temporary file) or an in-memory ``store``.
    """

    def __init__(self, db_path=DEFAULT_KNOWLEDGE_DB, store=None):
        if store is not None:
            self.store = store
        elif db_path is not None:
            self.store = KnowledgeStore(db_path=db_path)
        else:
            self.store = KnowledgeStore()  # isolated :memory:
        self.store.load_from_repository()

    # -- internals ---------------------------------------------------------

    def _require_node(self, node_id):
        if not isinstance(node_id, str) or not node_id:
            raise KnowledgeArgumentError(
                "node_id must be a non-empty string")
        node = self.store.get(node_id)
        if node is None:
            raise NodeNotFoundError(node_id)
        return node

    @staticmethod
    def _validate_limit(limit):
        if limit is None:
            return None
        if not _is_positive_int(limit):
            raise KnowledgeArgumentError(
                "limit must be a positive integer or null")
        return limit

    @staticmethod
    def _validate_node_type(node_type):
        if node_type is None:
            return None
        if node_type not in VALID_TYPES:
            raise KnowledgeArgumentError(
                "invalid node type %r; expected one of %s"
                % (node_type, sorted(VALID_TYPES)))
        return node_type

    # -- search ------------------------------------------------------------

    def search(self, query, node_type=None, limit=None):
        """Full-text search with deterministic ordering.

        Preserves the repository's ``search_nodes`` scoring (word occurrence in
        id/type/name/description/metadata). Results are ordered by score
        (descending) then node id (ascending), and each result retains its
        ``_score`` so callers can surface ranking. An empty query returns ``[]``.

        Ranking/filtering/limiting run on lightweight fields; only the final
        returned nodes are hydrated (batched provenance + relationships).
        """
        if not isinstance(query, str):
            raise KnowledgeArgumentError(
                "query must be a string")
        node_type = self._validate_node_type(node_type)
        limit = self._validate_limit(limit)

        ranked = self.store.repo.search_rankings(query)
        if node_type is not None:
            ranked = [r for r in ranked if r[2] == node_type]
        ranked.sort(key=lambda r: (-r[0], r[1]))
        if limit is not None:
            ranked = ranked[:limit]
        hydrated = {n["id"]: n for n in
                    self.store.repo.hydrate_nodes([r[1] for r in ranked])}
        results = []
        for score, node_id, _type in ranked:
            node = hydrated[node_id]
            node["_score"] = score
            results.append(node)
        return results

    # -- get ---------------------------------------------------------------

    def get(self, node_id):
        """Return one node (type, metadata, relationships, provenance)."""
        return self._require_node(node_id)

    # -- related -----------------------------------------------------------

    def related(self, node_id, limit=None):
        """Deterministic neighbours of a node via existing relationships.

        A related node is any node connected by a relationship in either
        direction (outgoing source, or incoming target). Results are ordered
        by node id; each entry lists the relationships that connect it.
        """
        self._require_node(node_id)
        limit = self._validate_limit(limit)
        neighbours = {}
        for rel in self.store.relationships_of(node_id):
            target = rel.get("target")
            if not target or target == node_id:
                continue
            entry = neighbours.setdefault(target, {"node": None, "via": []})
            entry["via"].append({"relationship_type": rel["type"],
                                 "direction": "outgoing"})
        for other in self.store.all():
            if other["id"] == node_id:
                continue
            for rel in other.get("relationships", []):
                if rel.get("target") == node_id:
                    entry = neighbours.setdefault(
                        other["id"], {"node": None, "via": []})
                    entry["via"].append({"relationship_type": rel["type"],
                                         "direction": "incoming"})
        result = []
        for rid in sorted(neighbours):
            entry = neighbours[rid]
            entry["node"] = self.store.get(rid)
            entry["via"].sort(key=lambda v: (v["relationship_type"],
                                             v["direction"]))
            result.append(entry)
        if limit is not None:
            result = result[:limit]
        return result

    # -- follow ------------------------------------------------------------

    def follow(self, node_id, relationship_type=None):
        """Existing relationship traversal from a node.

        Only relationships whose target node is loaded are returned (the
        repository/store's existing semantics). Order is deterministic:
        relationship type then target node id.
        """
        self._require_node(node_id)
        if relationship_type is not None and relationship_type \
                not in RELATIONSHIP_KINDS:
            raise RelationshipTypeError(relationship_type, RELATIONSHIP_KINDS)
        out = []
        for rel, target in self.store.follow(node_id, relationship_type):
            out.append({
                "relationship_type": rel["type"],
                "target_node_id": rel["target"],
                "label": rel.get("label"),
                "node": target,
            })
        out.sort(key=lambda r: (r["relationship_type"],
                                r["target_node_id"]))
        return out

    # -- inspect -----------------------------------------------------------

    def inspect(self):
        """Database facts: source/node/relationship counts by type."""
        repo = self.store.repo
        return {
            "source_count": repo.conn.execute(
                "SELECT COUNT(*) FROM sources").fetchone()[0],
            "node_count": repo.conn.execute(
                "SELECT COUNT(*) FROM nodes").fetchone()[0],
            "relationship_count": repo.conn.execute(
                "SELECT COUNT(*) FROM relationships").fetchone()[0],
            "nodes_by_type": dict(repo.conn.execute(
                "SELECT type, COUNT(*) FROM nodes GROUP BY type").fetchall()),
            "relationships_by_type": dict(repo.conn.execute(
                "SELECT relationship_type, COUNT(*) FROM relationships "
                "GROUP BY relationship_type").fetchall()),
        }

    # -- provenance --------------------------------------------------------

    def provenance(self, node_id):
        """Source/provenance for a node (never invented; only what exists)."""
        node = self._require_node(node_id)
        prov = node.get("provenance") or {}
        return {
            "node_id": node_id,
            "source_id": prov.get("source_id"),
            "source_name": prov.get("source_name"),
            "source_version": prov.get("source_version"),
            "source_location": prov.get("source_location"),
            "imported_at": prov.get("imported_at"),
            "evidence_references": node.get("evidence_references", []),
            "evidence_reference_count": node.get("evidence_reference_count", 0),
        }

    # -- lifecycle ---------------------------------------------------------

    def close(self):
        """Release the underlying repository connection (read-only)."""
        if isinstance(self.store.repo, KnowledgeRepository):
            self.store.repo.close()