"""Universal type and relationship vocabulary for the Knowledge Engine.

This module is the **single source of truth** for recommended node types and
relationship kinds.  It distinguishes three tiers:

* **Core** — the original Knowledge Schema v1 types and relationships.
* **Domain** — commonly needed domain-specific additions.
* **Recommended** — the union of core + domain (what users should prefer).

The node ``type`` field remains **free-form TEXT** in the database and in the
public contract.  Custom types are always accepted; the vocabulary merely
recommends a shared vocabulary so that heterogeneous sources interoperate.

Usage::

    from retrieval.vocabulary import (
        is_recommended_node_type,
        is_valid_node_type,
        is_empty_node_type,
        is_recommended_relationship_kind,
        is_valid_relationship_kind,
        is_empty_relationship_kind,
    )
"""

# ── Node types ──────────────────────────────────────────────────────────────

#: Original Knowledge Schema v1 types — always supported.
CORE_NODE_TYPES = frozenset({
    "concept",
    "technology",
    "entity",
    "procedure",
    "rule",
    "example",
    "dependency",
})

#: Domain-specific types commonly needed for universal ingestion.
DOMAIN_NODE_TYPES = frozenset({
    "person",
    "company",
    "product",
    "document",
    "event",
    "research_paper",
    "location",
    "discipline",
})

#: Union of core + domain — the recommended vocabulary.
ALL_RECOMMENDED_NODE_TYPES = CORE_NODE_TYPES | DOMAIN_NODE_TYPES

# ── Relationship kinds ──────────────────────────────────────────────────────

#: Original Knowledge Schema v1 relationship kinds — always supported.
CORE_RELATIONSHIP_KINDS = frozenset({
    "depends_on",
    "related_to",
    "part_of",
    "instance_of",
    "implements",
    "extends",
    "uses",
    "example_of",
    "references",
})

#: Domain-specific relationship kinds commonly needed for universal ingestion.
DOMAIN_RELATIONSHIP_KINDS = frozenset({
    "works_at",
    "founded",
    "authored",
    "created",
    "owns",
    "located_at",
    "cites",
})

#: Union of core + domain — the recommended vocabulary.
ALL_RECOMMENDED_RELATIONSHIP_KINDS = CORE_RELATIONSHIP_KINDS | DOMAIN_RELATIONSHIP_KINDS


# ── Node type helpers ───────────────────────────────────────────────────────

def is_recommended_node_type(value):
    """Return True if *value* is a recommended node type (core or domain)."""
    return isinstance(value, str) and value in ALL_RECOMMENDED_NODE_TYPES


def is_valid_node_type(value):
    """Return True if *value* is acceptable as a node type.

    Any non-empty string is valid — the ``type`` field is free-form.
    Empty strings, None, and non-strings are invalid.
    """
    return isinstance(value, str) and bool(value.strip())


def is_empty_node_type(value):
    """Return True if *value* is None or an empty/whitespace-only string."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


# ── Relationship kind helpers ───────────────────────────────────────────────

def is_recommended_relationship_kind(value):
    """Return True if *value* is a recommended relationship kind."""
    return isinstance(value, str) and value in ALL_RECOMMENDED_RELATIONSHIP_KINDS


def is_valid_relationship_kind(value):
    """Return True if *value* is acceptable as a relationship kind.

    Any non-empty string is valid — relationship kinds are free-form.
    Empty strings, None, and non-strings are invalid.
    """
    return isinstance(value, str) and bool(value.strip())


def is_empty_relationship_kind(value):
    """Return True if *value* is None or an empty/whitespace-only string."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False
