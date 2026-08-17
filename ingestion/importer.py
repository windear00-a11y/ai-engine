"""Ingestion importer: structured source file -> SQLite knowledge database.

Pipeline (per source file):
    parse  ->  validate  ->  normalize  ->  SQLite transaction  ->  summary

The importer is *atomic for a single source*: validation failures never touch
the database, and a failure during the SQL transaction (bad relationship, FK
violation, duplicate id) rolls the whole source back so no partial source is
left behind.

No AI, no embeddings, no vector search, no network -- purely deterministic file
I/O and SQL.
"""

import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dataclasses import dataclass, field

from ingestion.validator import validate_source
from retrieval.repository import KnowledgeRepository


@dataclass
class ImportSummary:
    source: str
    valid: bool
    nodes_imported: int = 0
    relationships_imported: int = 0
    source_id: int = None
    errors: list = field(default_factory=list)
    path: str = None

    def as_dict(self):
        return {
            "source": self.source,
            "path": self.path,
            "valid": self.valid,
            "source_id": self.source_id,
            "nodes_imported": self.nodes_imported,
            "relationships_imported": self.relationships_imported,
            "errors": [e.as_dict() if hasattr(e, "as_dict") else e
                       for e in self.errors],
        }


def _count_relationships(nodes):
    return sum(len(n.get("relationships", [])) for n in nodes)


def import_source_file(repo, path, location=None, clear=False):
    """Validate and import a single source file into ``repo``.

    Returns an :class:`ImportSummary`. On validation failure the database is
    left untouched; on a transaction failure the partial source is rolled back.
    """
    path = os.path.abspath(path)
    summary = ImportSummary(source="", valid=False, path=path)

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        summary.errors.append({"code": "file_not_found",
                               "message": f"file not found: {path}", "path": path})
        return summary
    except json.JSONDecodeError as e:
        summary.errors.append({"code": "invalid_json",
                               "message": f"invalid JSON: {e}", "path": path})
        return summary
    except Exception as e:
        summary.errors.append({"code": "read_error",
                               "message": str(e), "path": path})
        return summary

    result = validate_source(data)
    if not result.valid:
        summary.source = (result.source or {}).get("name", "")
        summary.errors.extend(result.errors)
        return summary

    norm = result.source
    name = norm["name"]
    # Provenance: never invented. Use the explicitly provided location if
    # present, otherwise the real on-disk path of the imported file.
    loc = norm.get("location") or location or path
    version = norm.get("version")
    metadata = norm.get("metadata") or {}
    if norm.get("description") is not None:
        metadata = dict(metadata)
        metadata.setdefault("description", norm["description"])
    nodes = result.nodes

    summary.source = name
    summary.valid = True

    repo.initialize()
    if clear:
        repo.clear()
    try:
        sid = repo.import_source(
            source_name=name,
            source_location=loc,
            nodes=nodes,
            source_version=version,
            source_metadata=metadata,
        )
    except Exception as e:
        summary.valid = False
        summary.errors.append({
            "code": "import_failed",
            "message": f"import failed (rolled back): {e}",
            "path": path,
        })
        return summary

    summary.source_id = sid
    summary.nodes_imported = len(nodes)
    summary.relationships_imported = _count_relationships(nodes)
    return summary


def import_source_data(repo, data, location="<in-memory>"):
    """Validate + import an already-parsed source dict (used by tests)."""
    result = validate_source(data)
    if not result.valid:
        return ImportSummary(
            source=(result.source or {}).get("name", ""),
            valid=False, errors=result.errors)
    norm = result.source
    name = norm["name"]
    loc = norm.get("location") or location
    repo.initialize()
    try:
        sid = repo.import_source(
            source_name=name,
            source_location=loc,
            nodes=result.nodes,
            source_version=norm.get("version"),
            source_metadata=norm.get("metadata") or {},
        )
    except Exception as e:
        return ImportSummary(source=name, valid=False, errors=[
            {"code": "import_failed",
             "message": f"import failed (rolled back): {e}",
             "path": location}])
    return ImportSummary(
        source=name, valid=True, source_id=sid,
        nodes_imported=len(result.nodes),
        relationships_imported=_count_relationships(result.nodes))
