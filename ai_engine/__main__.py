"""ai_engine -- command-line client for the Knowledge API.

The CLI is ONLY a client/interface over :class:`api.KnowledgeAPI`. It contains
no database logic, no raw SQLite access, no shell execution, and no arbitrary
command execution -- every command maps 1:1 to an approved API operation.

Default database: the canonical ``database/knowledge.db`` (overridable with
``--db`` so tests use isolated temporary databases).

Commands
--------
    python -m ai_engine search "Python exceptions" [--type TYPE] [--limit N]
    python -m ai_engine get <node_id>
    python -m ai_engine related <node_id> [--limit N]
    python -m ai_engine follow <node_id> [--type REL_TYPE]
    python -m ai_engine provenance <node_id>
    python -m ai_engine inspect
    python -m ai_engine --help

Output is machine-readable JSON (stable key order). Errors are JSON on stdout
with a human-readable line on stderr and a non-zero exit code.
"""

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.knowledge_api import KnowledgeAPI
from api.errors import KnowledgeError
from retrieval.repository import DEFAULT_KNOWLEDGE_DB


def to_json(payload):
    """Stable, deterministic JSON serialization."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _build_api(args):
    db = getattr(args, "db", None) or DEFAULT_KNOWLEDGE_DB
    return KnowledgeAPI(db_path=db)


def _run(args, operation, *op_args, **op_kwargs):
    api = _build_api(args)
    try:
        result = getattr(api, operation)(*op_args, **op_kwargs)
    except KnowledgeError as e:
        api.close()
        print(to_json(e.as_dict()))
        print("error: %s" % e, file=sys.stderr)
        return 1
    api.close()
    print(to_json(result))
    return 0


def cmd_inspect(args):
    return _run(args, "inspect")


def cmd_search(args):
    return _run(args, "search", args.query, node_type=args.type,
                limit=args.limit)


def cmd_get(args):
    return _run(args, "get", args.node_id)


def cmd_related(args):
    return _run(args, "related", args.node_id, limit=args.limit)


def cmd_follow(args):
    return _run(args, "follow", args.node_id, args.rel_type)


def cmd_provenance(args):
    return _run(args, "provenance", args.node_id)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="ai_engine",
        description="Knowledge API client (deterministic, read-only).")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("search", help="full-text search over knowledge nodes")
    p.add_argument("query", help="search query text")
    p.add_argument("--type", dest="type", default=None,
                   help="filter by node type")
    p.add_argument("--limit", dest="limit", type=int, default=None,
                   help="maximum number of results")
    p.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB,
                   help="database file (default: database/knowledge.db)")
    p.set_defaults(func=cmd_search)

    for name, cmd, help_text in (
        ("get", cmd_get, "return one node by id"),
        ("provenance", cmd_provenance, "return provenance for a node"),
    ):
        pp = sub.add_parser(name, help=help_text)
        pp.add_argument("node_id", help="node identifier")
        pp.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB,
                        help="database file (default: database/knowledge.db)")
        pp.set_defaults(func=cmd)

    p = sub.add_parser("related", help="return related nodes")
    p.add_argument("node_id", help="node identifier")
    p.add_argument("--limit", dest="limit", type=int, default=None,
                   help="maximum number of results")
    p.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB,
                   help="database file (default: database/knowledge.db)")
    p.set_defaults(func=cmd_related)

    p = sub.add_parser("follow", help="follow relationships from a node")
    p.add_argument("node_id", help="node identifier")
    p.add_argument("--type", default=None, dest="rel_type",
                   help="relationship type filter (e.g. extends)")
    p.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB,
                   help="database file (default: database/knowledge.db)")
    p.set_defaults(func=cmd_follow)

    p = sub.add_parser("inspect", help="database facts and counts")
    p.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB,
                   help="database file (default: database/knowledge.db)")
    p.set_defaults(func=cmd_inspect)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())