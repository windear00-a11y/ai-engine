"""Transport-independent tool interface over the Knowledge API.

Architecture::

    Tool request -> validated operation -> KnowledgeAPI -> structured response

External software (an agent, tool-call adapter, local process, future HTTP or
socket transport) sends one structured request and receives one structured
response. The interface:

* validates the request strictly (:mod:`api.contract`) -- unknown operations,
  missing/extra arguments, wrong types, invalid limits and unsupported
  relationship types are all rejected before any API call;
* never accepts filesystem paths, SQL, or commands as arguments;
* executes exactly one approved, read-only KnowledgeAPI operation;
* never raises for contract violations -- every failure becomes an
  ``ok: false`` response with a stable error code and message (no stack
  traces leak into the public contract);
* contains no HTTP/transport code; it is a plain object that any transport
  can wrap.

The module also exposes a simple local runner for testing::

    python -m api.tools request.json          # read one request from a file
    python -m api.tools --db other.db req.json
    echo '{"operation":"inspect"}' | python -m api.tools -
"""

import argparse
import json
import sys

from api.contract import (
    CONTRACT_VERSION,
    DEFAULT_SEARCH_LIMIT,
    validate_request,
)
from api.errors import InternalError, KnowledgeError, ToolRequestError
from api.knowledge_api import KnowledgeAPI
from retrieval.repository import DEFAULT_KNOWLEDGE_DB


def _error_payload(exc):
    """Convert a KnowledgeError into the contract's flattened error shape."""
    d = dict(exc.as_dict())
    code = d.pop("error", "knowledge_error")
    message = d.pop("message", str(exc))
    payload = {"code": code, "message": message}
    payload.update(d)  # structured extras: node_id, valid, operation, ...
    return payload


class ToolInterface:
    """Executes validated tool requests against a KnowledgeAPI instance.

    Construction (one of):
        ToolInterface()                    # default database
        ToolInterface(db_path="path.db")   # explicit database file
        ToolInterface(api=some_api)        # reuse an existing KnowledgeAPI

    ``execute(request)`` always returns a response dict; it never raises.
    """

    def __init__(self, db_path=DEFAULT_KNOWLEDGE_DB, api=None):
        if api is not None:
            self._api = api
            self._owns_api = False
        else:
            self._api = KnowledgeAPI(db_path=db_path)
            self._owns_api = True

    @property
    def api(self):
        """The underlying KnowledgeAPI (the documented boundary below tools)."""
        return self._api

    def close(self):
        """Release the underlying API/database connection if owned."""
        if self._owns_api:
            self._api.close()

    # -- public execute ----------------------------------------------------

    def execute(self, request):
        """Run one approved operation and return a structured response.

        Never raises: validation and API failures become ``ok: false``
        responses with stable error codes.
        """
        try:
            operation, arguments = validate_request(request)
            result = self._dispatch(operation, arguments)
        except KnowledgeError as exc:
            return self._failed(request, exc)
        except Exception:  # noqa: BLE001 - contract boundary never leaks
            return self._failed(request, InternalError())
        return {
            "ok": True,
            "operation": operation,
            "contract_version": CONTRACT_VERSION,
            "result": result,
        }

    # -- internals ----------------------------------------------------------

    def _dispatch(self, operation, arguments):
        # operation is contract-validated; method names are derived safely.
        return getattr(self, "_op_" + operation)(**arguments)

    @staticmethod
    def _failed(request, exc):
        operation = None
        if isinstance(request, dict) and isinstance(request.get("operation"), str):
            operation = request["operation"]
        return {
            "ok": False,
            "operation": operation,
            "contract_version": CONTRACT_VERSION,
            "error": _error_payload(exc),
        }

    # -- operation handlers (thin delegates; no duplicated knowledge logic) -

    def _op_search(self, query, node_type=None, limit=None):
        if limit is None:
            limit = DEFAULT_SEARCH_LIMIT
        return self._api.search(query, node_type=node_type, limit=limit)

    def _op_get(self, node_id):
        return self._api.get(node_id)

    def _op_related(self, node_id, limit=None):
        return self._api.related(node_id, limit=limit)

    def _op_follow(self, node_id, relationship_type=None):
        return self._api.follow(node_id, relationship_type)

    def _op_provenance(self, node_id):
        return self._api.provenance(node_id)

    def _op_inspect(self):
        return self._api.inspect()


def execute(request, db_path=DEFAULT_KNOWLEDGE_DB):
    """Convenience: run one request against a fresh interface and close it.

    Returns the structured response dict (never raises for contract errors).
    """
    interface = ToolInterface(db_path=db_path)
    try:
        return interface.execute(request)
    finally:
        interface.close()


def _emit_json(payload):
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="api.tools",
        description="Local Knowledge Engine tool-call runner (testing only).")
    parser.add_argument("request_file", nargs="?",
                        help="path to a request JSON file, or '-' for stdin "
                             "(default: read stdin)")
    parser.add_argument("--db", default=DEFAULT_KNOWLEDGE_DB,
                        help="database file (default: database/knowledge.db)")
    args = parser.parse_args(argv)

    try:
        if args.request_file and args.request_file != "-":
            with open(args.request_file, "r", encoding="utf-8") as f:
                text = f.read()
        else:
            text = sys.stdin.read()
        try:
            request = json.loads(text)
        except ValueError as exc:
            _emit_json(ToolInterface._failed(
                {}, ToolRequestError("invalid JSON: %s" % exc)))
            return 1
    except OSError as exc:
        _emit_json(ToolInterface._failed(
            {}, ToolRequestError("unable to read request: %s" % exc)))
        return 1

    interface = ToolInterface(db_path=args.db)
    try:
        response = interface.execute(request)
    finally:
        interface.close()
    _emit_json(response)
    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())