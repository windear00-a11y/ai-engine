"""Deterministic validation for external import data files.

``validate_external`` checks a parsed external JSON dict and returns an
:class:`ExternalValidationResult` containing structured errors, warnings,
and normalized output.  No database, no network, no AI — fully deterministic.

External import format (JSON)
-----------------------------
::

    {
      "source": {
        "name": "required-string",
        "version": "optional-string",
        "location": "optional-string"
      },
      "nodes": [
        {
          "id": "required-string",
          "type": "required-string",
          "name": "required-string",
          "description": "optional-string",
          "metadata": {}               # optional, JSON-compatible
        }
      ],
      "relationships": [
        {
          "source_node_id": "required-string",
          "relationship_type": "required-string",
          "target_node_id": "required-string",
          "label": "optional-string-or-null"
        }
      ]
    }

Validation rules
----------------
Source:
  * ``source`` is required and must be an object.
  * ``source.name`` is required, non-empty string.
  * ``source.version`` / ``source.location`` (if present) must be strings.

Nodes:
  * ``nodes`` is required and must be a non-empty list.
  * Each node ``id`` is required, non-empty string, unique within the file.
  * Each node ``type`` is required, non-empty string (free-form).
  * Each node ``name`` is required, non-empty string.
  * ``description`` is optional (any string, including empty).
  * ``metadata`` (if present) must be a JSON-compatible dict.
  * Extra top-level node keys are preserved as metadata.

Relationships:
  * ``relationships`` is optional; defaults to [].
  * Each relationship ``source_node_id`` must reference a node in the file.
  * Each relationship ``target_node_id`` must reference a node in the file.
  * ``relationship_type`` is required, non-empty string.
  * ``label`` is optional (string or null).
  * Duplicate (source, type, target) triples are errors.
  * Self-references (source == target) are allowed but flagged as warnings.

Conflict detection (without database)
--------------------------------------
  * Duplicate node IDs within the file.
  * Duplicate relationship triples within the file.
  * Duplicate source names within the file (single-file: N/A).
"""

from dataclasses import dataclass, field


@dataclass
class ExternalValidationError:
    """One deterministic validation finding."""
    code: str
    message: str
    path: str

    def as_dict(self):
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass
class ExternalValidationWarning:
    """One non-fatal finding."""
    code: str
    message: str
    path: str

    def as_dict(self):
        return {"code": self.code, "message": self.message, "path": self.path}


@dataclass
class ExternalValidationResult:
    """Outcome of validating one external import file."""
    valid: bool
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    source: dict = None
    nodes: list = field(default_factory=list)
    relationships: list = field(default_factory=list)

    def as_dict(self):
        return {
            "valid": self.valid,
            "errors": [e.as_dict() for e in self.errors],
            "warnings": [w.as_dict() for w in self.warnings],
            "source_name": (self.source or {}).get("name"),
            "node_count": len(self.nodes),
            "relationship_count": len(self.relationships),
            "node_types": _type_counts(self.nodes),
        }


def _type_counts(nodes):
    counts = {}
    for n in nodes:
        t = n.get("type")
        if t:
            counts[t] = counts.get(t, 0) + 1
    return counts


def _is_nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _is_string(value):
    return isinstance(value, str)


def _is_json_compatible(value):
    """Return True if *value* is valid JSON-compatible data."""
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return True
    if isinstance(value, str):
        return True
    if isinstance(value, list):
        return all(_is_json_compatible(v) for v in value)
    if isinstance(value, dict):
        return all(isinstance(k, str) and _is_json_compatible(v)
                    for k, v in value.items())
    return False


def validate_external(data):
    """Validate a parsed external import dict; return :class:`ExternalValidationResult`.

    Pure, deterministic, read-only.  No database interaction.
    """
    errors = []
    warnings = []

    # -- top-level structure ------------------------------------------------
    if not isinstance(data, dict):
        return ExternalValidationResult(
            False,
            [ExternalValidationError("invalid_root",
                                     "top-level JSON must be an object", "")],
            None, [], [])

    # -- source metadata ---------------------------------------------------
    src = data.get("source")
    norm_source = None
    if not isinstance(src, dict):
        errors.append(ExternalValidationError(
            "source_metadata", "'source' must be an object", "source"))
    else:
        name = src.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(ExternalValidationError(
                "source_metadata",
                "'source.name' is required and must be a non-empty string",
                "source.name"))
        version = src.get("version")
        if version is not None and not isinstance(version, str):
            errors.append(ExternalValidationError(
                "source_metadata",
                "'source.version' must be a string when provided",
                "source.version"))
        location = src.get("location")
        if location is not None and not isinstance(location, str):
            errors.append(ExternalValidationError(
                "source_metadata",
                "'source.location' must be a string when provided",
                "source.location"))
        norm_source = {
            "name": (name or "").strip(),
            "version": version,
            "location": location,
        }

    # -- nodes -------------------------------------------------------------
    nodes = data.get("nodes")
    if not isinstance(nodes, list):
        errors.append(ExternalValidationError(
            "nodes", "'nodes' must be a list", "nodes"))
        nodes = []
    elif len(nodes) == 0:
        errors.append(ExternalValidationError(
            "nodes", "'nodes' must contain at least one node", "nodes"))

    seen_ids = {}
    normalized_nodes = []
    for i, node in enumerate(nodes):
        npath = f"nodes[{i}]"
        if not isinstance(node, dict):
            errors.append(ExternalValidationError(
                "malformed_node", "node must be an object", npath))
            continue

        # id
        raw_id = node.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            errors.append(ExternalValidationError(
                "node_id",
                "node 'id' is required and must be a non-empty string",
                f"{npath}.id"))
            nid = None
        else:
            nid = raw_id.strip()
            if nid in seen_ids:
                errors.append(ExternalValidationError(
                    "duplicate_id",
                    f"duplicate node id {nid!r} (first at nodes[{seen_ids[nid]}])",
                    f"{npath}.id"))
            else:
                seen_ids[nid] = i

        # type (free-form: any non-empty string)
        ntype = node.get("type")
        ntype_norm = ntype.strip().lower() if isinstance(ntype, str) else ntype
        if ntype is None or (isinstance(ntype, str) and not ntype.strip()):
            errors.append(ExternalValidationError(
                "node_type",
                "node 'type' is required and must be a non-empty string",
                f"{npath}.type"))
        elif not isinstance(ntype, str):
            errors.append(ExternalValidationError(
                "node_type",
                f"node 'type' must be a string, got {type(ntype).__name__}",
                f"{npath}.type"))
        else:
            ntype_norm = ntype.strip().lower()

        # name
        nm = node.get("name")
        if not isinstance(nm, str) or not nm.strip():
            errors.append(ExternalValidationError(
                "node_name",
                "node 'name' is required and must be a non-empty string",
                f"{npath}.name"))

        # description (optional, any string)
        desc = node.get("description")
        if desc is not None and not isinstance(desc, str):
            errors.append(ExternalValidationError(
                "node_description",
                "node 'description' must be a string when provided",
                f"{npath}.description"))

        # metadata (optional, JSON-compatible dict)
        metadata = node.get("metadata")
        if metadata is not None:
            if not isinstance(metadata, dict):
                errors.append(ExternalValidationError(
                    "node_metadata",
                    "node 'metadata' must be an object when provided",
                    f"{npath}.metadata"))
            elif not _is_json_compatible(metadata):
                errors.append(ExternalValidationError(
                    "node_metadata",
                    "node 'metadata' contains non-JSON-compatible values",
                    f"{npath}.metadata"))

        # Collect extra keys as metadata
        known_keys = {"id", "type", "name", "description", "metadata"}
        extras = {k: v for k, v in node.items() if k not in known_keys}
        if metadata is not None and isinstance(metadata, dict):
            extras.update(metadata)

        normalized_nodes.append({
            "id": nid,
            "type": ntype_norm if isinstance(ntype, str) else ntype,
            "name": nm,
            "description": desc if isinstance(desc, str) else (desc if desc is not None else ""),
            "metadata": extras,
        })

    # -- relationships -----------------------------------------------------
    rels_raw = data.get("relationships")
    if rels_raw is None:
        rels_raw = []
    if not isinstance(rels_raw, list):
        errors.append(ExternalValidationError(
            "relationships", "'relationships' must be a list", "relationships"))
        rels_raw = []

    normalized_rels = []
    seen_rel_keys = set()
    for j, rel in enumerate(rels_raw):
        rpath = f"relationships[{j}]"
        if not isinstance(rel, dict):
            errors.append(ExternalValidationError(
                "relationship_structure",
                "relationship must be an object", rpath))
            continue

        src_id = rel.get("source_node_id")
        if not isinstance(src_id, str) or not src_id.strip():
            errors.append(ExternalValidationError(
                "relationship_source",
                "relationship 'source_node_id' is required and must be a "
                "non-empty string", f"{rpath}.source_node_id"))

        tgt_id = rel.get("target_node_id")
        if not isinstance(tgt_id, str) or not tgt_id.strip():
            errors.append(ExternalValidationError(
                "relationship_target",
                "relationship 'target_node_id' is required and must be a "
                "non-empty string", f"{rpath}.target_node_id"))

        rtype = rel.get("relationship_type")
        if not isinstance(rtype, str) or not rtype.strip():
            errors.append(ExternalValidationError(
                "relationship_type",
                "relationship 'relationship_type' is required and must be a "
                "non-empty string", f"{rpath}.relationship_type"))

        label = rel.get("label")

        norm_rel = {
            "source_node_id": src_id.strip() if isinstance(src_id, str) else src_id,
            "relationship_type": rtype.strip() if isinstance(rtype, str) else rtype,
            "target_node_id": tgt_id.strip() if isinstance(tgt_id, str) else tgt_id,
            "label": label,
        }
        normalized_rels.append(norm_rel)

        # Self-reference warning
        if (isinstance(src_id, str) and isinstance(tgt_id, str)
                and src_id.strip() == tgt_id.strip()):
            warnings.append(ExternalValidationWarning(
                "self_reference",
                f"relationship from {src_id.strip()!r} to itself",
                rpath))

        # Duplicate relationship key
        key = (norm_rel["source_node_id"], norm_rel["relationship_type"],
               norm_rel["target_node_id"])
        if key in seen_rel_keys:
            errors.append(ExternalValidationError(
                "duplicate_relationship",
                f"duplicate relationship {key!r}", rpath))
        else:
            seen_rel_keys.add(key)

    # -- referential integrity (relationships -> nodes) --------------------
    valid_ids = set(seen_ids.keys())
    for j, rel in enumerate(normalized_rels):
        rpath = f"relationships[{j}]"
        src = rel["source_node_id"]
        if isinstance(src, str) and src and src not in valid_ids:
            errors.append(ExternalValidationError(
                "relationship_source_missing",
                f"relationship source node {src!r} is not defined in 'nodes'",
                f"{rpath}.source_node_id"))
        tgt = rel["target_node_id"]
        if isinstance(tgt, str) and tgt and tgt not in valid_ids:
            errors.append(ExternalValidationError(
                "relationship_target_missing",
                f"relationship target node {tgt!r} is not defined in 'nodes'",
                f"{rpath}.target_node_id"))

    return ExternalValidationResult(
        valid=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        source=norm_source if len(errors) == 0 else None,
        nodes=normalized_nodes if len(errors) == 0 else [],
        relationships=normalized_rels if len(errors) == 0 else [],
    )
