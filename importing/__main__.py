"""Knowledge Import Verification Layer CLI (Safe Import Dry-Run v1).

Usage:
    python -m importing verify   <import_plan.json>
    python -m importing dry-run  <import_plan.json>
    python -m importing import   <import_plan.json> --db <db> [--backup-dir DIR] [--yes]

``verify`` and ``dry-run`` are STRICTLY read-only:
    * they only READ the given ``import_plan.json``,
    * ``dry-run`` uses an isolated in-memory KnowledgeRepository that is
      discarded when it finishes,
    * the production database (``database/knowledge.db``) is never opened,
      created, or modified.

``import`` is the ONE command allowed to write: it re-verifies the plan,
refuses anything not SAFE, creates a timestamped (never-overwritten) backup,
and applies the ENTIRE source inside a single SQLite transaction, then
re-verifies from disk. It refuses to write unless ``--yes`` is given.

Each command prints the machine-readable JSON report to stdout, then the
human-readable summary.
"""

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from importing.plan_loader import load_plan, PlanLoadError
from importing.verifier import verify_plan
from importing.dry_run import dry_run
from importing.importer import import_plan, human_import_summary
from importing.report import build_report, human_summary, to_json
from retrieval.repository import DEFAULT_KNOWLEDGE_DB


# The acceptance/import verification layers never write to SQLite. This is the
# same contract the acceptance layer enforces; the importing CLI only ever
# constructs ``:memory:`` repositories. The single exception is the explicit
# ``import`` command, which is the designated production write path.
SQLITE_WRITES_FORBIDDEN = True


def _guard():
    if not SQLITE_WRITES_FORBIDDEN:
        raise RuntimeError("import verification must stay read-only")


def cmd_verify(path):
    plan = load_plan(path)
    verification = verify_plan(plan)
    report = build_report(verification)
    print(to_json(report))
    print(human_summary(report))
    return 0 if report["safe"] else 1


def cmd_dry_run(path):
    plan = load_plan(path)
    result = dry_run(plan)
    report = build_report(result.verification, result)
    print(to_json(report))
    print(human_summary(report))
    return 0 if report["safe"] else 1


def cmd_import(path, db, backup_dir, yes):
    if not yes:
        report = {
            "safe": False,
            "error": "refusing to write without --yes "
                     "(the import command modifies the database)",
        }
        print(to_json(report))
        return 1
    result = import_plan(path, db, backup_dir=backup_dir)
    print(to_json(result.as_dict()))
    print(human_import_summary(result))
    return 0 if result.committed else 1


def main(argv=None):
    _guard()
    parser = argparse.ArgumentParser(
        prog="importing",
        description="Import plan verification and controlled import.")
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("verify", "statically verify an import plan (read-only)"),
        ("dry-run", "simulate the import in an isolated repository (read-only)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("import_plan",
                       help="path to the acceptance import_plan.json")

    p = sub.add_parser(
        "import", help="apply a verified plan to a database (WRITES)")
    p.add_argument("import_plan",
                   help="path to the acceptance import_plan.json")
    p.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB,
                   help="target database file (default: database/knowledge.db)")
    p.add_argument("--backup-dir", default=None,
                   help="directory for timestamped backups "
                        "(default: <db_dir>/backups)")
    p.add_argument("--yes", action="store_true",
                   help="confirm the write; import refuses without this flag")

    args = parser.parse_args(argv)
    try:
        if args.command == "verify":
            return cmd_verify(args.import_plan)
        if args.command == "import":
            return cmd_import(args.import_plan, args.db, args.backup_dir,
                              args.yes)
        return cmd_dry_run(args.import_plan)
    except PlanLoadError as e:
        print(to_json({"error": str(e)}))
        return 1
    except FileNotFoundError as e:
        print(to_json({"error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
