"""Knowledge Acceptance Layer CLI.

Deterministic acceptance decisions + import preview over Knowledge Compiler
candidate output. Never writes to SQLite -- the safety guard is wired into
every command.

Usage:
    python -m acceptance evaluate <candidate-output> [--output DIR]
    python -m acceptance preview  <candidate-output> [--output DIR]
    python -m acceptance audit    <candidate-output> [--output DIR]
    python -m acceptance prepare  <candidate-output> --output DIR

``<candidate-output>`` is the output of ``python -m knowledge_compiler
candidates`` (its ``--output`` root) or a single candidates JSON file.

Every command prints a machine-readable JSON summary to stdout. With
``--output DIR`` the full records are also written deterministically:
``acceptance_evaluation.json`` (evaluate), ``acceptance_preview.json``
(preview), ``acceptance_audit.json`` (audit), or ``import_plan.json``
(prepare). ``prepare`` always requires ``--output``.
"""

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from acceptance.loader import load_candidates, CandidateLoadError
from acceptance.evaluator import EvaluationResult
from acceptance.report import (
    build_summary, build_preview, build_audit, to_json,
)
from acceptance.safety import guard_sqlite_read_only, SQLITE_WRITES_FORBIDDEN


def _write(dirpath, name, payload):
    os.makedirs(dirpath, exist_ok=True)
    with open(os.path.join(dirpath, name), "w", encoding="utf-8") as f:
        f.write(to_json(payload))
        f.write("\n")


def cmd_evaluate(target, output=None):
    candidates, valid = load_candidates(target)
    result = EvaluationResult(candidates, valid)
    summary = build_summary(result)
    if output:
        payload = {
            "source": os.path.abspath(target),
            "summary": summary,
            "decisions": [d.as_dict() for d in result.decisions],
            "identities": [g.as_dict() for g in result.groups],
        }
        _write(output, "acceptance_evaluation.json", payload)
    print(to_json(summary))
    return 0


def cmd_preview(target, output=None):
    candidates, valid = load_candidates(target)
    result = EvaluationResult(candidates, valid)
    preview = build_preview(result)
    preview["source"] = os.path.abspath(target)
    if output:
        payload = {
            "source": os.path.abspath(target),
            "preview": preview,
            "decisions": [d.as_dict() for d in result.decisions],
        }
        _write(output, "acceptance_preview.json", payload)
    print(to_json(preview))
    return 0


def cmd_audit(target, output=None):
    candidates, valid = load_candidates(target)
    result = EvaluationResult(candidates, valid)
    audit = build_audit(result)
    audit["source"] = os.path.abspath(target)
    audit["summary"] = build_summary(result)
    if output:
        payload = {
            "source": os.path.abspath(target),
            "audit": audit,
            "decisions": [d.as_dict() for d in result.decisions],
        }
        _write(output, "acceptance_audit.json", payload)
    print(to_json(audit))
    return 0


def cmd_prepare(target, output):
    """Write the import plan (nodes + relationships + provenance) as JSON.

    This is a PREVIEW on disk -- it never touches SQLite. It is the exact,
    byte-identical payload a future importer would apply, so it can be
    reviewed before any database write is ever introduced.
    """
    candidates, valid = load_candidates(target)
    result = EvaluationResult(candidates, valid)
    preview = build_preview(result)
    payload = {
        "source": os.path.abspath(target),
        "read_only_guard": {
            "sqlite_writes_forbidden": SQLITE_WRITES_FORBIDDEN,
            "note": "preview only -- no database was written",
        },
        "preview": preview,
        "decisions": [d.as_dict() for d in result.decisions],
        "identities": [g.as_dict() for g in result.groups],
    }
    _write(output, "import_plan.json", payload)
    print(to_json(preview))
    return 0


def main(argv=None):
    guard_sqlite_read_only()
    parser = argparse.ArgumentParser(
        prog="acceptance",
        description="Deterministic knowledge acceptance layer (v1).")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("evaluate", "preview", "audit"):
        p = sub.add_parser(name, help=f"{name} candidate output")
        p.add_argument("candidate_output",
                       help="knowledge_compiler candidates output dir or file")
        p.add_argument("--output", default=None,
                       help="directory for full records (default: stdout only)")

    p = sub.add_parser("prepare", help="write import plan JSON (preview only)")
    p.add_argument("candidate_output",
                   help="knowledge_compiler candidates output dir or file")
    p.add_argument("--output", required=True,
                   help="directory for import_plan.json (REQUIRED)")

    args = parser.parse_args(argv)
    try:
        if args.command == "evaluate":
            return cmd_evaluate(args.candidate_output, args.output)
        if args.command == "audit":
            return cmd_audit(args.candidate_output, args.output)
        if args.command == "prepare":
            return cmd_prepare(args.candidate_output, args.output)
        return cmd_preview(args.candidate_output, args.output)
    except (CandidateLoadError, FileNotFoundError, json.JSONDecodeError) as e:
        print(to_json({"error": str(e)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
