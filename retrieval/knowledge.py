"""KnowledgeStore retrieval module.

Structured knowledge store backed by a persistent repository.
"""

import json
import os

from .repository import KnowledgeRepository, DEFAULT_KNOWLEDGE_DB
from .vocabulary import (
    ALL_RECOMMENDED_NODE_TYPES,
    ALL_RECOMMENDED_RELATIONSHIP_KINDS,
)

VALID_TYPES = ALL_RECOMMENDED_NODE_TYPES

RELATIONSHIP_KINDS = ALL_RECOMMENDED_RELATIONSHIP_KINDS


class KnowledgeStore:
    """Structured knowledge store backed by a persistent repository.

    Persistence goes through :class:`KnowledgeRepository` (currently SQLite),
    so the storage backend can be swapped (e.g. PostgreSQL) without touching
    the tools. For fast, deterministic search and graph traversal the store
    also keeps an in-memory mirror of the loaded nodes; that mirror is the
    authoritative source for the public ``get``/``search``/``follow`` APIs and
    preserves the original JSON-file behavior exactly.

    Usage:
        store = KnowledgeStore()                 # isolated :memory: SQLite
        store = KnowledgeStore(db_path="db.db")  # file-backed SQLite
        store = KnowledgeStore(repository=repo)   # explicit repository
        store.load("knowledge/")                  # import JSON into the repo
        store.load_from_repository()              # load everything from the repo
    """

    def __init__(self, repository=None, db_path=None):
        if repository is not None:
            self.repo = repository
        elif db_path is not None:
            self.repo = KnowledgeRepository(db_path)
        else:
            # Isolated per-instance database keeps callers (and tests) independent.
            self.repo = KnowledgeRepository(":memory:")
        self.repo.initialize()
        self.nodes = {}    # id -> record (with _source added)
        self.errors = []   # list of (path, message)
        self._order = []   # insertion order of ids

    def load(self, directory):
        for root, _, files in os.walk(directory):
            for file in sorted(files):
                if not file.endswith(".json"):
                    continue
                self._load_file(os.path.join(root, file))
        return self

    def _load_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            self.errors.append((path, f"invalid JSON: {e}"))
            return

        if not isinstance(data, dict):
            self.errors.append((path, "top-level JSON must be an object"))
            return

        ok, msg = self._validate(data)
        if not ok:
            self.errors.append((path, msg))
            return

        node_id = data["id"]
        if node_id in self.nodes:
            self.errors.append((path, f"duplicate node id: {node_id}"))
            return

        # Persist to the repository (SQLite) within a transaction. On failure
        # the import is rolled back and the node is not added to the mirror.
        try:
            self.repo.import_node(os.path.basename(path), path, data)
        except Exception as e:
            self.errors.append((path, f"import failed: {e}"))
            return

        data["_source"] = path
        data.setdefault("relationships", [])
        self.nodes[node_id] = data
        self._order.append(node_id)

    def load_from_repository(self):
        """Rebuild the in-memory mirror from the persistent repository."""
        self.nodes = {}
        self._order = []
        self.errors = []
        for node in self.repo.get_all_nodes():
            self.nodes[node["id"]] = node
            self._order.append(node["id"])
        return self

    @staticmethod
    def _validate(data):
        node_id = data.get("id")
        if not isinstance(node_id, str) or not node_id:
            return False, "missing or invalid 'id'"

        node_type = data.get("type")
        if node_type not in VALID_TYPES:
            return False, f"missing or invalid 'type' (got {node_type!r})"

        rels = data.get("relationships")
        if rels is not None:
            if not isinstance(rels, list):
                return False, "'relationships' must be a list"
            for rel in rels:
                if not isinstance(rel, dict):
                    return False, "each relationship must be an object"
                if not isinstance(rel.get("type"), str) or not rel["type"]:
                    return False, "relationship missing 'type'"
                if not isinstance(rel.get("target"), str) or not rel["target"]:
                    return False, "relationship missing 'target'"

        return True, ""

    def get(self, node_id):
        return self.nodes.get(node_id)

    def all(self):
        return [self.nodes[i] for i in self._order]

    def search(self, query, limit=None):
        words = [w for w in query.lower().split() if w]
        results = []
        for node_id in self._order:
            node = self.nodes[node_id]
            text = json.dumps(node, ensure_ascii=False).lower()
            score = sum(1 for w in words if w in text)
            if score > 0:
                results.append((score, node))
        results.sort(key=lambda x: x[0], reverse=True)
        if limit is not None:
            results = results[:limit]
        return results

    def relationships_of(self, node_id):
        node = self.nodes.get(node_id)
        if node is None:
            return []
        return list(node.get("relationships", []))

    def follow(self, node_id, rel_type=None):
        """Return resolved (relationship, target_node) pairs for a node.

        Only relationships whose target node is loaded are returned.
        """
        node = self.nodes.get(node_id)
        if node is None:
            return []
        out = []
        for rel in node.get("relationships", []):
            if rel_type is not None and rel.get("type") != rel_type:
                continue
            target = self.nodes.get(rel.get("target"))
            if target is not None:
                out.append((rel, target))
        return out
