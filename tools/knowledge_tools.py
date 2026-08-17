"""Knowledge Tools Layer.

A thin, deterministic interface over :class:`retrieval.knowledge.KnowledgeStore`.
These tools are the boundary between the externalized knowledge system and
future consumers (planners, coding agents, automation, or an optional AI
model). They never load knowledge themselves -- they delegate to a
``KnowledgeStore`` instance -- and they always return structured, serializable
results rather than formatted text.

Input/output contracts
-----------------------
All tools return plain ``dict`` objects (JSON-serializable). On invalid input
or unknown ids, tools return a structured error (``{"error": ...}``) instead
of raising, so they are safe to call from automated systems.

knowledge.search(query, limit=None)
    Input : query (str, non-empty); limit (int | None)
    Output: {"query", "count", "results": [{"id","name","type","description","score"}]}

knowledge.get(node_id)
    Input : node_id (str, non-empty)
    Output: {"found": true,  "id","name","type","description","source","relationships"}
            {"found": false, "error"}

knowledge.related(node_id)
    Input : node_id (str, non-empty)
    Output: {"node_id","found","count","relationships":[{"type","target","label"}]}
            {"found": false, "error"}

knowledge.follow(node_id, max_depth=1, rel_type=None)
    Input : node_id (str, non-empty); max_depth (int >= 0); rel_type (str | None)
    Output: {"start","max_depth","rel_type","error",
             "nodes":[{"id","name","type","depth"}],
             "edges":[{"from","type","to","label"}],
             "depth_reached"}
"""

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from retrieval.knowledge import KnowledgeStore

DEFAULT_KNOWLEDGE_DIR = os.path.join(_ROOT, "knowledge")


class KnowledgeTools:
    """Deterministic tool interface backed by a ``KnowledgeStore``."""

    def __init__(self, store=None, knowledge_dir=None):
        if store is not None:
            self.store = store
        else:
            self.store = KnowledgeStore().load(
                knowledge_dir or DEFAULT_KNOWLEDGE_DIR
            )

    # -- internal helpers -------------------------------------------------

    @staticmethod
    def _validate_id(node_id):
        if not isinstance(node_id, str) or not node_id:
            return "node_id must be a non-empty string"
        return None

    def _node_view(self, node):
        return {
            "id": node["id"],
            "name": node.get("name"),
            "type": node.get("type"),
            "description": node.get("description"),
            "source": node.get("_source"),
            "provenance": node.get("provenance"),
            "relationships": list(node.get("relationships", [])),
        }

    def _result_view(self, node, score=None):
        view = {
            "id": node["id"],
            "name": node.get("name"),
            "type": node.get("type"),
            "description": node.get("description"),
        }
        if score is not None:
            view["score"] = score
        return view

    # -- tools ------------------------------------------------------------

    def search(self, query, limit=None):
        """Search the knowledge base by query; return ranked nodes."""
        if not isinstance(query, str) or not query.strip():
            return {"query": query, "count": 0, "results": [],
                    "error": "query must be a non-empty string"}
        if limit is not None and (not isinstance(limit, int) or limit < 0):
            return {"query": query, "count": 0, "results": [],
                    "error": "limit must be a non-negative int"}
        raw = self.store.search(query, limit)
        results = [self._result_view(node, score) for score, node in raw]
        return {"query": query, "count": len(results), "results": results}

    def get(self, node_id):
        """Retrieve a single knowledge node by id."""
        err = self._validate_id(node_id)
        if err:
            return {"found": False, "error": err}
        node = self.store.get(node_id)
        if node is None:
            return {"found": False, "error": f"unknown node id: {node_id}"}
        view = self._node_view(node)
        view["found"] = True
        return view

    def related(self, node_id):
        """Return the first-class relationships of a node."""
        err = self._validate_id(node_id)
        if err:
            return {"node_id": node_id, "found": False, "count": 0,
                    "relationships": [], "error": err}
        node = self.store.get(node_id)
        if node is None:
            return {"node_id": node_id, "found": False, "count": 0,
                    "relationships": [], "error": f"unknown node id: {node_id}"}
        rels = [dict(r) for r in node.get("relationships", [])]
        return {"node_id": node_id, "found": True, "count": len(rels),
                "relationships": rels}

    def follow(self, node_id, max_depth=1, rel_type=None):
        """Follow relationships from a node with bounded, cycle-safe traversal.

        Returns the nodes reached and the edges traversed. Traversal stops at
        ``max_depth`` hops and never revisits a node, so cycles cannot cause
        infinite traversal.
        """
        err = self._validate_id(node_id)
        if err:
            return {"start": node_id, "max_depth": max_depth,
                    "rel_type": rel_type, "error": err, "nodes": [],
                    "edges": [], "depth_reached": 0}
        if not isinstance(max_depth, int) or max_depth < 0:
            return {"start": node_id, "max_depth": max_depth,
                    "rel_type": rel_type,
                    "error": "max_depth must be a non-negative int",
                    "nodes": [], "edges": [], "depth_reached": 0}
        if rel_type is not None and not isinstance(rel_type, str):
            return {"start": node_id, "max_depth": max_depth,
                    "rel_type": rel_type,
                    "error": "rel_type must be a string or None",
                    "nodes": [], "edges": [], "depth_reached": 0}
        if self.store.get(node_id) is None:
            return {"start": node_id, "max_depth": max_depth,
                    "rel_type": rel_type,
                    "error": f"unknown node id: {node_id}",
                    "nodes": [], "edges": [], "depth_reached": 0}

        visited = {node_id}
        depth_of = {node_id: 0}
        queue = [(node_id, 0)]
        edges = []
        depth_reached = 0

        while queue:
            current, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            for rel in self.store.relationships_of(current):
                if rel_type is not None and rel.get("type") != rel_type:
                    continue
                target = rel.get("target")
                edges.append({
                    "from": current,
                    "type": rel.get("type"),
                    "to": target,
                    "label": rel.get("label"),
                })
                if target in visited:
                    continue
                nxt_depth = depth + 1
                if nxt_depth <= max_depth:
                    visited.add(target)
                    depth_of[target] = nxt_depth
                    depth_reached = max(depth_reached, nxt_depth)
                    queue.append((target, nxt_depth))

        nodes = []
        for nid in visited:
            node = self.store.get(nid)
            nodes.append({
                "id": nid,
                "name": node.get("name") if node else None,
                "type": node.get("type") if node else None,
                "depth": depth_of.get(nid, 0),
            })

        return {
            "start": node_id,
            "max_depth": max_depth,
            "rel_type": rel_type,
            "error": None,
            "nodes": nodes,
            "edges": edges,
            "depth_reached": depth_reached,
        }


def _default_tools():
    global _DEFAULT_TOOLS
    if _DEFAULT_TOOLS is None:
        _DEFAULT_TOOLS = KnowledgeTools()
    return _DEFAULT_TOOLS


_DEFAULT_TOOLS = None


# Module-level convenience wrappers (``knowledge.search`` style API).
def search(query, limit=None):
    return _default_tools().search(query, limit)


def get(node_id):
    return _default_tools().get(node_id)


def related(node_id):
    return _default_tools().related(node_id)


def follow(node_id, max_depth=1, rel_type=None):
    return _default_tools().follow(node_id, max_depth, rel_type)
