"""Knowledge Schema mapping -- ACCEPT candidates -> canonical node types.

The canonical Knowledge Schema v1 node types live in
``retrieval.knowledge.VALID_TYPES``:

    concept, technology, entity, procedure, rule, example, dependency

Mapping is deliberately conservative and purely deterministic. Only ACCEPTED
candidates are mapped, and only where an existing, justified mapping exists.
The api_declaration mapping is directive-aware -- a node type is only created
when the candidate's evidence deterministically justifies it:

    api_declaration (module)                -> technology
    api_declaration (class/exception/c:type/c:struct) -> entity
    api_declaration (other directives)      -> UNMAPPABLE (HELD by evaluator)
    definition      -> concept      (a glossary term/definition is a concept)
    dependency      -> dependency   (exact canonical type)
    procedure       -> procedure    (exact canonical type)
    code_example    -> example      (exact canonical type)
    inheritance     -> RELATIONSHIP "extends" (class -> base), no node of its own

Only ``module``-level declarations warrant a ``technology`` node; classes /
exception types / C structs are ``entity`` nodes; a plain function, method,
data, attribute, macro, or opcode is a declared *interface surface* with no
justifiable standalone node type, so it is NOT forced in -- it stays HELD by
the evaluator. No new schema is invented. Nothing here writes to any database;
this module only *proposes* the nodes/relationships that an import preview
would create.
"""

from acceptance.types import compute_identity
from retrieval.knowledge import VALID_TYPES  # noqa: F401  (canonical schema)

# api_declaration directive -> canonical node type. ``None`` means the
# directive has no safe deterministic node type -> the candidate stays HELD
# (reuse HOLD_UNMAPPED) rather than being forced into an unjustified node.
API_DIRECTIVE_NODE_TYPE = {
    "module": "technology",
    "class": "entity",
    "exception": "entity",
    "c:type": "entity",
    "c:struct": "entity",
}

# ACCEPT-eligible kind -> canonical node type. ``None`` means the kind maps to
# a relationship only (or has no safe mapping -> HELD by the evaluator).
NODE_TYPE_FOR_KIND = {
    "api_declaration": "technology",  # overridden per-directive below
    "definition": "concept",
    "dependency": "dependency",
    "procedure": "procedure",
    "code_example": "example",
    "inheritance": None,
}

# ACCEPT-eligible kind -> proposed relationship: (rel_type, source_meta_key,
# target_meta_key). The source/target meta keys carry the documented class /
# base names; their node ids follow the api_declaration identity convention so
# they line up with the proposed nodes of any accepted api_declaration.
RELATIONSHIP_FOR_KIND = {
    "inheritance": ("extends", "class", "base"),
}

# Directives that never map to a standalone node. Kept explicit so the audit
# report can explain exactly why each HELD api_declaration was held.
UNMAPPED_API_DIRECTIVES = (
    "function", "method", "classmethod", "staticmethod", "decorator",
    "data", "attribute", "opcode", "c:function", "c:var", "c:macro",
    "c:member",
)


def node_type_for(kind, meta=None):
    """Canonical node type for an ACCEPTed kind, or None when unmappable.

    ``meta`` makes the api_declaration mapping directive-aware: a ``module``
    directive yields ``technology``; ``class``/``exception``/``c:type``/
    ``c:struct`` yield ``entity``; all other directives yield None (HELD).
    """
    if kind == "api_declaration":
        directive = (meta or {}).get("directive")
        if not isinstance(directive, str):
            return None
        return API_DIRECTIVE_NODE_TYPE.get(directive)
    return NODE_TYPE_FOR_KIND.get(kind)


def relationship_for(kind):
    """Return (rel_type, source_meta_key, target_meta_key) or None."""
    return RELATIONSHIP_FOR_KIND.get(kind)


def is_mappable(kind, meta=None):
    """A candidate is mappable when it yields a node and/or a relationship."""
    return node_type_for(kind, meta) is not None or relationship_for(kind) is not None


def _api_identity(name, directive="class"):
    """Identity fingerprint of an api_declaration for a symbol name.

    ``directive`` defaults to ``"class"`` because inheritance endpoints are
    class declarations -- their fingerprints must match the identity of the
    corresponding accepted api_declaration candidates (which now include the
    directive in the identity fields).
    """
    return compute_identity("api_declaration",
                            {"meta": {"directive": directive, "name": name,
                                      "signature": name},
                             "evidence": ""})


def _resolve_endpoint(name, name_to_node_ids, directive="class"):
    """Resolve an inheritance endpoint name to exactly one accepted class id.

    ``name_to_node_ids`` maps a symbol name to the sorted node identities of
    ALL accepted (mappable) api_declaration nodes for that name. A name
    resolves to the ``directive`` identity only when that is the name's ONLY
    accepted node identity -- zero -> None (unresolved / not accepted),
    multiple -> None (ambiguous), or the accepted node is of a different
    directive/type -> None (the ``extends`` edge needs a class node).
    """
    if not isinstance(name, str) or not name:
        return None
    ids = name_to_node_ids.get(name) or []
    identity = _api_identity(name, directive)
    if len(ids) != 1 or ids[0] != identity:
        return None
    return identity


def resolve_inheritance_endpoints(meta, name_to_node_ids):
    """Resolve ``class``/``base`` to accepted class node ids or None.

    Returns (source_node_id, target_node_id). An endpoint resolves only when
    exactly ONE accepted, mappable class node identity exists for its name --
    no placeholder/invented node is ever used to satisfy an edge.
    """
    return (_resolve_endpoint(meta.get("class"), name_to_node_ids),
            _resolve_endpoint(meta.get("base"), name_to_node_ids))


def proposed_node(identity, kind, meta, name, description):
    """Build a canonical proposed node dict (existing node types only)."""
    ntype = node_type_for(kind, meta)
    if ntype is None:
        return None
    return {
        "id": identity,
        "type": ntype,
        "name": name,
        "description": description,
    }


def proposed_relationships(identity, kind, meta, resolved=None):
    """Build proposed relationship dicts for one canonical identity.

    ``inheritance`` yields one ``extends`` edge from the class node to the base
    node. The edge endpoints are the ACCEPTED node ids resolved by the
    evaluator (``resolved``), which never invents a node: an edge is only
    proposed when both endpoints resolved to accepted nodes. Without a resolved
    endpoint mapping the legacy ``_api_identity(class/base)`` convention is
    used (handy for direct unit tests).
    """
    rel = relationship_for(kind)
    if rel is None:
        return []
    rel_type, source_key, target_key = rel
    source = meta.get(source_key)
    target = meta.get(target_key)
    if not source or not target:
        return []
    if isinstance(resolved, dict) and resolved.get("source_node_id") \
            and resolved.get("target_node_id"):
        source_id = resolved["source_node_id"]
        target_id = resolved["target_node_id"]
    else:
        source_id = _api_identity(source)
        target_id = _api_identity(target)
    return [{
        "source_node_id": source_id,
        "relationship_type": rel_type,
        "target_node_id": target_id,
        "label": "%s %s %s" % (source, rel_type, target),
        "target_name": target,
    }]
