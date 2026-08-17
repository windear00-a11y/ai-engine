"""Canonical input format for a knowledge source (Ingestion v1).

A *source* is a single structured file describing one body of knowledge. The
ingestion pipeline (``ingestion.validator`` / ``ingestion.importer``) consumes
this exact shape and turns it into rows in the SQLite knowledge database.

Source format (JSON)
--------------------
{
  "source": {                       # required source metadata (provenance)
    "name": "python-core",          # required, non-empty string
    "version": "1.0",               # optional, string
    "location": "file://...",       # optional, string (real location only)
    "description": "Core Python concepts",
    "...": <any extra metadata>     # optional, preserved verbatim
  },
  "nodes": [                        # required, non-empty list of nodes
    {
      "id": "python",               # required, unique non-empty string
      "type": "technology",         # required, one of VALID_TYPES
      "name": "Python",             # required, non-empty string
      "description": "...",         # required, non-empty string
      "...": <any extra fields>,    # optional, preserved as node metadata
      "relationships": [            # optional, list of edges
        {"type": "related_to", "target": "python-stdlib", "label": "ships with"}
      ]
    }
  ]
}

Node types (the canonical Knowledge Schema v1 -- single source of truth in
``retrieval.knowledge.VALID_TYPES``):

    concept, technology, entity, procedure, rule, example, dependency

Relationship edges are first-class: each relationship is an object with a
``type`` (non-empty string) and a ``target`` node id (non-empty string) that
MUST reference a node defined in the same source. An optional ``label`` string
may annotate the edge. The set of well-known relationship kinds lives in
``retrieval.knowledge.RELATIONSHIP_KINDS`` and is used for documentation; the
importer validates structure and target existence but does not freeze the
vocabulary, so new edge types can be introduced without a schema change.
"""

from retrieval.knowledge import VALID_TYPES, RELATIONSHIP_KINDS

# Version of this ingestion source format. Bumped if the shape changes.
SOURCE_FORMAT_VERSION = "1.0"

# Node fields that are required in every source node.
REQUIRED_NODE_FIELDS = ("id", "type", "name", "description")

# Source metadata fields understood by the importer.
SOURCE_META_FIELDS = ("name", "version", "location", "description")


def build_source(name, nodes, version=None, location=None,
                 description=None, metadata=None):
    """Construct a canonical source dict (handy for tests / programmatic use)."""
    source = {"name": name}
    if version is not None:
        source["version"] = version
    if location is not None:
        source["location"] = location
    if description is not None:
        source["description"] = description
    if metadata:
        source.update(metadata)
    return {"source": source, "nodes": nodes}


def build_node(node_id, node_type, name, description, relationships=None,
               **extras):
    """Construct a canonical node dict with optional extra metadata fields."""
    node = {
        "id": node_id,
        "type": node_type,
        "name": name,
        "description": description,
    }
    if relationships is not None:
        node["relationships"] = relationships
    node.update(extras)
    return node
