"""ai_engine -- command-line client for the Knowledge API.

The CLI is ONLY a client/interface over :class:`api.KnowledgeAPI`. It contains
no database logic, no raw SQLite access, no shell execution, and no arbitrary
command execution -- every command maps 1:1 to an approved API operation.

Default database: the canonical ``database/knowledge.db`` (overridable with
``--db`` so tests use isolated temporary databases).

Commands
--------
    python -m ai_engine search "Python exceptions" [--type TYPE] [--limit N] [--verbose]
    python -m ai_engine get <node_id>
    python -m ai_engine related <node_id> [--limit N]
    python -m ai_engine follow <node_id> [--type REL_TYPE]
    python -m ai_engine provenance <node_id>
    python -m ai_engine inspect
    python -m ai_engine --help

Output is machine-readable JSON (stable key order). Errors are JSON on stdout
with a human-readable line on stderr and a non-zero exit code.

``search`` is compact by default: each result carries only minimal summary
fields (id, type, name, summary, score), so large evidence/content blobs are
never dumped to the terminal. Use ``--verbose`` for fuller node payloads and
``get <node_id>`` for the complete node with provenance and evidence. Result
counts are bounded by a default limit and a hard maximum; verbose output is
additionally capped by a byte budget (see constants below).
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

# -- search output safety -------------------------------------------------
# ``search`` never dumps full node payloads to the terminal by default.
DEFAULT_SEARCH_LIMIT = 20            # default result count when --limit omitted
MAX_SEARCH_LIMIT = 100               # hard cap on any --limit value
_SUMMARY_PREVIEW = 160               # chars of description shown in summaries
_VERBOSE_FIELD_CAP = 2000            # chars per string field in --verbose output
_VERBOSE_LIST_CAP = 50               # items per list field in --verbose output
_MAX_VERBOSE_BYTES = 512 * 1024      # hard byte budget for --verbose output


def to_json(payload):
    """Stable, deterministic JSON serialization."""
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _build_api(args):
    db = getattr(args, "db", None) or DEFAULT_KNOWLEDGE_DB
    return KnowledgeAPI(db_path=db)


def _run(args, operation, *op_args, present=None, **op_kwargs):
    api = _build_api(args)
    try:
        result = getattr(api, operation)(*op_args, **op_kwargs)
    except KnowledgeError as e:
        api.close()
        print(to_json(e.as_dict()))
        print("error: %s" % e, file=sys.stderr)
        return 1
    api.close()
    payload = result if present is None else present(result)
    print(to_json(payload))
    return 0


def _search_limit(args):
    """Bounded result count for a search invocation.

    Omitted ``--limit`` uses :data:`DEFAULT_SEARCH_LIMIT`; any positive
    ``--limit`` is capped at :data:`MAX_SEARCH_LIMIT` so no invocation can
    request an unbounded dump. Invalid (non-positive) limits pass through so
    the API reports a structured ``invalid_argument`` error.
    """
    if args.limit is None:
        return DEFAULT_SEARCH_LIMIT
    if args.limit < 0:
        return args.limit
    return min(args.limit, MAX_SEARCH_LIMIT)


def search_summary(node):
    """Compact, identification-only view of a search result."""
    summary = {"id": node.get("id"), "type": node.get("type")}
    name = node.get("name")
    if name:
        summary["name"] = name
    description = (node.get("description") or "").strip()
    if description:
        preview = description[:_SUMMARY_PREVIEW]
        if len(description) > _SUMMARY_PREVIEW:
            preview += "..."
        summary["summary"] = preview
    score = node.get("_score")
    if score is not None:
        summary["score"] = score
    return summary


def _present_compact(results):
    """Default search presentation: summary fields only."""
    return [search_summary(node) for node in results]


def _clip(value):
    """Bound any single field's contribution to verbose output size."""
    if isinstance(value, str):
        if len(value) <= _VERBOSE_FIELD_CAP:
            return value
        return value[:_VERBOSE_FIELD_CAP] + "..."
    if isinstance(value, list):
        return [_clip(item) for item in value[:_VERBOSE_LIST_CAP]]
    if isinstance(value, dict):
        return {key: _clip(item) for key, item in value.items()}
    return value


def _present_verbose(results):
    """Fuller search presentation, hard-bounded by a byte budget."""
    body = []
    for node in results:
        clipped = _clip(node)
        if body and len(to_json(body + [clipped])) > _MAX_VERBOSE_BYTES:
            body.append({"_truncated": True,
                         "_omitted": len(results) - len(body)})
            return body
        body.append(clipped)
    return body


def cmd_inspect(args):
    return _run(args, "inspect")


def cmd_search(args):
    present = _present_verbose if args.verbose else _present_compact
    return _run(args, "search", args.query, node_type=args.type,
                limit=_search_limit(args), present=present)


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
                   help="maximum number of results (default %d, hard cap %d)"
                        % (DEFAULT_SEARCH_LIMIT, MAX_SEARCH_LIMIT))
    p.add_argument("--verbose", dest="verbose", action="store_true",
                   help="fuller node payloads (still size-bounded)")
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