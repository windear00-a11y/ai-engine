"""CLI for the ingestion pipeline.

Usage:
    python -m ingestion validate <file>
    python -m ingestion import  <file> [--db PATH]
    python -m ingestion inspect <file>

All commands print a structured JSON summary to stdout and use process exit
codes: 0 = success / valid, 1 = invalid / error. This keeps the CLI safe to
drive from automation and other tooling.
"""

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from ingestion.validator import validate_source
from ingestion.importer import import_source_file
from retrieval.repository import DEFAULT_KNOWLEDGE_DB


def _load(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def cmd_validate(path):
    try:
        data = _load(path)
    except FileNotFoundError:
        print(json.dumps({"valid": False, "errors": [
            {"code": "file_not_found", "message": f"file not found: {path}",
             "path": path}]}, indent=2))
        return 1
    except json.JSONDecodeError as e:
        print(json.dumps({"valid": False, "errors": [
            {"code": "invalid_json", "message": f"invalid JSON: {e}",
             "path": path}]}, indent=2))
        return 1
    result = validate_source(data)
    out = result.as_dict()
    out["source"] = (result.source or {}).get("name")
    print(json.dumps(out, indent=2))
    return 0 if result.valid else 1


def cmd_inspect(path):
    try:
        data = _load(path)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(json.dumps({"valid": False, "error": str(e)}, indent=2))
        return 1
    result = validate_source(data)
    if not result.valid:
        out = result.as_dict()
        out["source"] = (result.source or {}).get("name")
        print(json.dumps(out, indent=2))
        return 1
    norm = result.source
    out = {
        "source": norm.get("name"),
        "version": norm.get("version"),
        "location": norm.get("location"),
        "description": norm.get("description"),
        "metadata": norm.get("metadata"),
        "node_count": len(result.nodes),
        "node_types": result.as_dict()["node_types"],
        "relationship_count": result.as_dict()["relationship_count"],
        "nodes": [
            {
                "id": n["id"],
                "type": n["type"],
                "name": n["name"],
                "description": n["description"],
                "relationships": n["relationships"],
                "extras": n["_extras"],
            }
            for n in result.nodes
        ],
    }
    print(json.dumps(out, indent=2))
    return 0


def cmd_import(path, db):
    from retrieval.repository import KnowledgeRepository
    repo = KnowledgeRepository(db)
    repo.initialize()
    summary = import_source_file(repo, path)
    print(json.dumps(summary.as_dict(), indent=2))
    try:
        repo.close()
    except Exception:
        pass
    return 0 if (summary.valid and not summary.errors) else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ingestion", description="Knowledge ingestion pipeline (v1).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate", help="validate a source file")
    p_val.add_argument("file")

    p_imp = sub.add_parser("import", help="import a source file into SQLite")
    p_imp.add_argument("file")
    p_imp.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB,
                       help="SQLite database path (default: %(default)s)")

    p_ins = sub.add_parser("inspect", help="show a structured source summary")
    p_ins.add_argument("file")

    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args.file)
    if args.command == "import":
        return cmd_import(args.file, args.db)
    if args.command == "inspect":
        return cmd_inspect(args.file)
    return 2


if __name__ == "__main__":
    sys.exit(main())
