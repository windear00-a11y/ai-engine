"""Deterministic validation for ingestion source files.

``validate_source`` checks a parsed source dict and returns a
:class:`ValidationResult` containing structured errors (each with a stable
``code``, a human message, and a JSON-pointer-like ``path``) plus the
*normalized* source metadata and nodes when the source is valid.

Validation rules
----------------
Source metadata:
  * ``source`` must be an object.
  * ``source.name`` is required and must be a non-empty string.
  * ``source.version`` / ``source.location`` (if present) must be strings.

Nodes:
  * ``nodes`` must be a non-empty list.
  * Each node id must be a unique, non-empty string.
  * ``type`` must be one of the canonical node types.
  * ``name`` and ``description`` are required non-empty strings.
  * Extra node fields are preserved (and later stored as node metadata).

Relationships (inside a node):
  * Must be a list of objects.
  * Each edge needs a non-empty ``type`` and a non-empty ``target``.
  * ``label`` (if present) must be a string.
  * ``target`` MUST reference a node id defined in the *same* source
    (referential integrity is enforced at ingestion time, so a source is
    self-contained and never produces dangling edges).

No network, no AI, no fuzzy matching -- validation is fully deterministic.
"""

from dataclasses import dataclass, field

from ingestion.source_format import VALID_TYPES, SOURCE_FORMAT_VERSION


@dataclass
class ValidationError:
    code: str
    message: str
    path: str

    def as_dict(self):
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass
class ValidationResult:
    valid: bool
    errors: list = field(default_factory=list)
    source: dict = None          # normalized source metadata (or None)
    nodes: list = field(default_factory=list)  # normalized nodes (or [])

    def as_dict(self):
        return {
            "valid": self.valid,
            "errors": [e.as_dict() for e in self.errors],
            "node_count": len(self.nodes),
            "relationship_count": sum(len(n.get("relationships", []))
                                      for n in self.nodes),
            "node_types": _type_counts(self.nodes),
        }


def _type_counts(nodes):
    counts = {}
    for n in nodes:
        t = n.get("type")
        if t:
            counts[t] = counts.get(t, 0) + 1
    return counts


def validate_source(data):
    """Validate a parsed source dict; return a :class:`ValidationResult`."""
    errors = []

    if not isinstance(data, dict):
        return ValidationResult(
            False,
            [ValidationError("invalid_root",
                             "top-level JSON must be an object", "")],
            None, [])

    # -- source metadata --------------------------------------------------
    src = data.get("source")
    norm_source = None
    if not isinstance(src, dict):
        errors.append(ValidationError(
            "source_metadata", "'source' must be an object", "source"))
    else:
        name = src.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(ValidationError(
                "source_metadata",
                "'source.name' is required and must be a non-empty string",
                "source.name"))
        version = src.get("version")
        if version is not None and not isinstance(version, str):
            errors.append(ValidationError(
                "source_metadata",
                "'source.version' must be a string when provided",
                "source.version"))
        location = src.get("location")
        if location is not None and not isinstance(location, str):
            errors.append(ValidationError(
                "source_metadata",
                "'source.location' must be a string when provided",
                "source.location"))
        norm_source = {
            "name": (name or "").strip(),
            "version": version,
            "location": location,
            "description": src.get("description"),
            "metadata": {
                k: v for k, v in src.items()
                if k not in ("name", "version", "location", "description")
            },
        }

    # -- nodes ------------------------------------------------------------
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        errors.append(ValidationError(
            "nodes", "'nodes' must be a list", "nodes"))
        nodes = []
    elif len(nodes) == 0:
        errors.append(ValidationError(
            "nodes", "'nodes' must contain at least one node", "nodes"))

    seen_ids = {}
    normalized_nodes = []
    for i, node in enumerate(nodes):
        npath = f"nodes[{i}]"
        if not isinstance(node, dict):
            errors.append(ValidationError(
                "malformed_node", "node must be an object", npath))
            continue

        # id
        raw_id = node.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            errors.append(ValidationError(
                "node_id",
                "node 'id' is required and must be a non-empty string",
                f"{npath}.id"))
            nid = None
        else:
            nid = raw_id.strip()
            if nid in seen_ids:
                errors.append(ValidationError(
                    "duplicate_id",
                    f"duplicate node id {nid!r} (first at nodes[{seen_ids[nid]}])",
                    f"{npath}.id"))
            else:
                seen_ids[nid] = i

        # type (case-insensitive against the canonical set)
        ntype = node.get("type")
        ntype_norm = ntype.lower() if isinstance(ntype, str) else ntype
        if ntype_norm not in VALID_TYPES:
            errors.append(ValidationError(
                "node_type",
                f"invalid node type {ntype!r}; "
                f"expected one of {sorted(VALID_TYPES)}",
                f"{npath}.type"))

        # name / description
        nm = node.get("name")
        if not isinstance(nm, str) or not nm.strip():
            errors.append(ValidationError(
                "node_name",
                "node 'name' is required and must be a non-empty string",
                f"{npath}.name"))
        desc = node.get("description")
        if not isinstance(desc, str) or not desc.strip():
            errors.append(ValidationError(
                "node_description",
                "node 'description' is required and must be a non-empty string",
                f"{npath}.description"))

        # relationships
        rels = node.get("relationships")
        norm_rels = []
        if rels is None:
            norm_rels = []
        elif not isinstance(rels, list):
            errors.append(ValidationError(
                "relationship_structure",
                "'relationships' must be a list",
                f"{npath}.relationships"))
        else:
            for j, rel in enumerate(rels):
                rpath = f"{npath}.relationships[{j}]"
                if not isinstance(rel, dict):
                    errors.append(ValidationError(
                        "relationship_structure",
                        "relationship must be an object", rpath))
                    continue
                rtype = rel.get("type")
                if not isinstance(rtype, str) or not rtype.strip():
                    errors.append(ValidationError(
                        "relationship_type",
                        "relationship 'type' is required and must be a "
                        "non-empty string", f"{rpath}.type"))
                tgt = rel.get("target")
                if not isinstance(tgt, str) or not tgt.strip():
                    errors.append(ValidationError(
                        "relationship_target",
                        "relationship 'target' is required and must be a "
                        "non-empty string", f"{rpath}.target"))
                lbl = rel.get("label")
                if lbl is not None and not isinstance(lbl, str):
                    errors.append(ValidationError(
                        "relationship_label",
                        "relationship 'label' must be a string when provided",
                        f"{rpath}.label"))
                norm_rels.append({
                    "type": rtype.strip() if isinstance(rtype, str) else rtype,
                    "target": tgt.strip() if isinstance(tgt, str) else tgt,
                    "label": lbl,
                })

        normalized_nodes.append({
            "id": nid,
            "type": ntype_norm,
            "name": nm,
            "description": desc,
            "relationships": norm_rels,
            "_extras": {
                k: v for k, v in node.items()
                if k not in ("id", "type", "name", "description",
                             "relationships")
            },
        })

    # -- relationship target referential integrity ------------------------
    valid_ids = set(seen_ids.keys())
    for i, node in enumerate(normalized_nodes):
        nid = node["id"]
        for j, rel in enumerate(node["relationships"]):
            tgt = rel.get("target")
            if isinstance(tgt, str) and tgt and tgt not in valid_ids:
                errors.append(ValidationError(
                    "relationship_target",
                    f"relationship target {tgt!r} does not reference any node "
                    f"in this source", f"nodes[{i}].relationships[{j}].target"))

    return ValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        source=norm_source if len(errors) == 0 else None,
        nodes=normalized_nodes if len(errors) == 0 else [],
    )
