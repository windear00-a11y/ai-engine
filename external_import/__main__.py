"""CLI for external data import: validation, dry-run, staging, preview, apply.

Usage:
    python -m external_import validate  <file>
    python -m external_import dry-run   <file>       [--db PRODUCTION_DB]
    python -m external_import stage     <file>       [--db PRODUCTION_DB] [--staging STAGING_DB]
    python -m external_import preview   <staging_db> [--db PRODUCTION_DB]
    python -m external_import apply     <staging_db> --db PRODUCTION_DB

All commands print structured JSON to stdout and use process exit codes:
    0 = success / valid
    1 = invalid / error

``validate``  checks the external JSON structure without any database.
``dry-run``   validates AND compares against the production DB (read-only).
``stage``     creates a staging DB with new, non-conflicting items.
``preview``   reads the staging DB and reports what apply would change.
``apply``     atomically imports staged items into production (with backup).
"""

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from external_import.validator import validate_external
from external_import.dry_run import dry_run, _human_summary


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
    result = validate_external(data)
    out = result.as_dict()
    print(json.dumps(out, indent=2))
    return 0 if result.valid else 1


def cmd_dry_run(path, db):
    try:
        data = _load(path)
    except FileNotFoundError:
        print(json.dumps({"safe": False, "errors": [
            {"code": "file_not_found", "message": f"file not found: {path}",
             "path": path}]}, indent=2))
        return 1
    except json.JSONDecodeError as e:
        print(json.dumps({"safe": False, "errors": [
            {"code": "invalid_json", "message": f"invalid JSON: {e}",
             "path": path}]}, indent=2))
        return 1
    report = dry_run(data, db_path=db)
    print(report.as_json())
    print(_human_summary(report))
    return 0 if report.safe else 1


def cmd_stage(path, db, staging_path):
    try:
        data = _load(path)
    except FileNotFoundError:
        print(json.dumps({"success": False, "errors": [
            {"code": "file_not_found", "message": f"file not found: {path}",
             "path": path}]}, indent=2))
        return 1
    except json.JSONDecodeError as e:
        print(json.dumps({"success": False, "errors": [
            {"code": "invalid_json", "message": f"invalid JSON: {e}",
             "path": path}]}, indent=2))
        return 1

    from external_import.staging import create_staging, human_staging_summary
    result = create_staging(data, staging_path, db)
    print(result.as_json())
    print(human_staging_summary(result))
    return 0 if result.success else 1


def cmd_preview(staging_path, db):
    from external_import.preview import preview, human_preview_summary
    report = preview(staging_path, db)
    print(report.as_json())
    print(human_preview_summary(report))
    return 0 if report.safe else 1


def cmd_apply(staging_path, db):
    from external_import.apply import apply, human_apply_summary
    result = apply(staging_path, db)
    print(result.as_json())
    print(human_apply_summary(result))
    return 0 if result.committed else 1


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="external_import",
        description="External data import: validation, dry-run, staging, "
                    "preview, apply (Phase 2-3).")
    sub = parser.add_subparsers(dest="command", required=True)

    p_val = sub.add_parser("validate",
                           help="validate external JSON structure (no DB)")
    p_val.add_argument("file", help="path to external import JSON file")

    p_dr = sub.add_parser("dry-run",
                          help="validate + conflict detection (read-only DB)")
    p_dr.add_argument("file", help="path to external import JSON file")
    p_dr.add_argument("--db", default=None,
                      help="SQLite database path (default: database/knowledge.db)")

    p_stg = sub.add_parser("stage",
                           help="create staging DB with new importable items")
    p_stg.add_argument("file", help="path to external import JSON file")
    p_stg.add_argument("--db", default=None,
                       help="production SQLite database path")
    p_stg.add_argument("--staging", default=None,
                       help="output staging DB path (default: temp file)")

    p_pre = sub.add_parser("preview",
                           help="preview staged import (read-only)")
    p_pre.add_argument("staging_db", help="path to staging database")
    p_pre.add_argument("--db", default=None,
                       help="production SQLite database path")

    p_app = sub.add_parser("apply",
                           help="apply staged import to production (atomic)")
    p_app.add_argument("staging_db", help="path to staging database")
    p_app.add_argument("--db", required=True,
                       help="production SQLite database path")
    p_app.add_argument("--backup-dir", default=None,
                       help="backup directory (default: database/backups)")

    args = parser.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args.file)
    if args.command == "dry-run":
        return cmd_dry_run(args.file, args.db)
    if args.command == "stage":
        import tempfile
        staging = args.staging
        if staging is None:
            staging = os.path.join(tempfile.mkdtemp(), "staging.db")
        return cmd_stage(args.file, args.db, staging)
    if args.command == "preview":
        return cmd_preview(args.staging_db, args.db)
    if args.command == "apply":
        return cmd_apply(args.staging_db, args.db)
    return 2


if __name__ == "__main__":
    sys.exit(main())
