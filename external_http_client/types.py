"""Lightweight structural types (dicts) returned by the external HTTP client.

These mirror the documented response shapes of the tool contract
(:mod:`docs/public-api-v1.md`) and nothing else.  TypedDicts give users
editor/type-checker help without wrapping or copying the data the engine
already returns over HTTP.
"""

from typing import Any, Dict, List, NotRequired, Optional, TypedDict

__all__ = [
    "Provenance",
    "Relationship",
    "Node",
    "RelatedEntry",
    "ViaEdge",
    "FollowEdge",
    "InspectStats",
]


class Provenance(TypedDict):
    """Source/provenance record for one node (``provenance`` operation)."""

    node_id: str
    source_id: Optional[int]
    source_name: Optional[str]
    source_version: Optional[str]
    source_location: Optional[str]
    imported_at: Optional[str]
    evidence_references: NotRequired[List[Dict[str, Any]]]
    evidence_reference_count: NotRequired[int]


class Relationship(TypedDict):
    """One relationship edge carried inside a node payload."""

    type: str
    target: str
    label: Optional[str]


class Node(TypedDict):
    """One knowledge node (``get`` / ``search`` results)."""

    id: str
    type: str
    name: str
    description: Optional[str]
    source_id: int
    relationships: NotRequired[List[Relationship]]
    provenance: NotRequired[Dict[str, Any]]


class ViaEdge(TypedDict):
    """How a related node is connected (direction + relationship type)."""

    direction: str
    relationship_type: str


class RelatedEntry(TypedDict):
    """One neighbour in a ``related`` result."""

    node: Node
    via: List[ViaEdge]


class FollowEdge(TypedDict):
    """One edge returned by ``follow``."""

    relationship_type: str
    target_node_id: str
    label: Optional[str]
    node: Node


class InspectStats(TypedDict):
    """Aggregate database facts (``inspect`` operation)."""

    source_count: int
    node_count: int
    relationship_count: int
    nodes_by_type: Dict[str, int]
    relationships_by_type: Dict[str, int]
