"""Deterministic knowledge compiler.

Turns raw documentation source into extracted structure and evidence-backed
knowledge candidates, WITHOUT AI, embeddings, vector search, or network access.

Usage:
    python -m knowledge_compiler scan <source> [--output DIR] [--limit N]
    python -m knowledge_compiler extract <source> [--output DIR] [--limit N]
    python -m knowledge_compiler candidates <source> [--output DIR] [--limit N]
"""

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from knowledge_compiler.pipeline import Pipeline
from knowledge_compiler import output as out


def _print(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="knowledge_compiler",
        description="Deterministic source -> evidence -> candidates compiler.")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("scan", "extract", "candidates"):
        p = sub.add_parser(name, help=f"{name} a documentation source")
        p.add_argument("source")
        p.add_argument("--output", default="output",
                       help="output directory (default: %(default)s)")
        p.add_argument("--limit", type=int, default=None,
                       help="process at most N documents (deterministic order)")

    args = parser.parse_args(argv)
    pipe = Pipeline(output_root=args.output)

    if args.command == "scan":
        manifest = pipe.scan(args.source)
        _print(manifest)
        return 0

    if args.command == "extract":
        pipe.extract(args.source, report=True, limit=args.limit)
    elif args.command == "candidates":
        pipe.candidates(args.source, report=True, limit=args.limit)

    summary = pipe.summarize(args.source, args.command, limit=args.limit)
    out.write_report(args.output, summary)
    _print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())