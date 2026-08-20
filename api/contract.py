"""Knowledge Engine tool-call contract (transport-independent).

Defines the stable public interface external software uses to call the
Knowledge Engine without the CLI and without any knowledge of SQLite:

    Tool request -> validated operation -> KnowledgeAPI -> structured response

This module only describes and validates the contract. It performs NO data
access and NO database logic -- execution is delegated to
:class:`api.knowledge_api.KnowledgeAPI`, whose already-deterministic,
read-only behavior is preserved unchanged.

Request format (JSON object)::

    {
        "operation": "search",          # one of OPERATIONS
        "arguments": {                  # validated per operation spec, below
            "query": "exception",
            "limit": 10
        }
    }

Success response (JSON object)::

    {
        "ok": true,
        "operation": "search",
        "result": [...]
    }

Error response (JSON object, no stack traces)::

    {
        "ok": false,
        "operation": "search",
        "error": {"code": "invalid_argument", "message": "..."}
    }

Error codes
-----------
``invalid_request``           request is not a JSON object (or malformed)
``unknown_operation``         requested operation is not defined
``invalid_argument``          argument missing, wrong type, or out of range
``invalid_relationship_type`` relationship_type is not a canonical kind
``node_not_found``            the requested node id does not exist
``internal_error``            unexpected failure (never leaks a traceback)

Security invariants / rejections
--------------------------------
* Unknown operations are rejected (``unknown_operation``).
* ``arguments`` is strictly whitelisted: any key outside the operation's
  allowed set is rejected (``invalid_argument``), so requests can never
  smuggle ``sql`` / ``path`` / ``command`` style keys anywhere.
* Argument values are type-checked; limits must be positive integers;
  relationship types must be canonical kinds.
* There is deliberately NO operation that executes commands, reads
  filesystem paths, or runs arbitrary SQL.
"""

from api.errors import (
    KnowledgeArgumentError,
    RelationshipTypeError,
    ToolRequestError,
    UnknownOperationError,
)
from retrieval.knowledge import (
    RELATIONSHIP_KINDS as _VALID_RELATIONSHIP_KINDS,
    VALID_TYPES as _VALID_NODE_TYPES,
)

# Canonical type sets, reused from retrieval (single source of truth).
VALID_NODE_TYPES = frozenset(_VALID_NODE_TYPES)
VALID_RELATIONSHIP_KINDS = frozenset(_VALID_RELATIONSHIP_KINDS)

# Result-cap and default-limit used by the tool layer (independent safety
# net on top of the API's own limit handling; the CLI applies the same caps).
MAX_RESULTS = 100
DEFAULT_SEARCH_LIMIT = 20


# -- argument validators ---------------------------------------------------

def _is_string(value):
    return isinstance(value, str)


def _is_nonempty_string(value):
    return isinstance(value, str) and bool(value.strip())


def _is_positive_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_valid_node_type(value):
    return value in VALID_NODE_TYPES


def _is_valid_relationship_kind(value):
    return value in VALID_RELATIONSHIP_KINDS


# -- operation specifications ----------------------------------------------
#
# Each spec declares:
#   required   argument name -> validator
#   optional   argument name -> validator
#   limit_key  which optional limit applies (capped at MAX_RESULTS)
#
# Every argument name below is the ONLY name accepted for that operation.

OPERATIONS = {
    "search": {
        "required": {"query": _is_nonempty_string},
        "optional": {"node_type": _is_valid_node_type, "limit": _is_positive_int},
        "limit_key": "limit",
    },
    "get": {
        "required": {"node_id": _is_nonempty_string},
        "optional": {},
    },
    "related": {
        "required": {"node_id": _is_nonempty_string},
        "optional": {"limit": _is_positive_int},
        "limit_key": "limit",
    },
    "follow": {
        "required": {"node_id": _is_nonempty_string},
        "optional": {"relationship_type": _is_valid_relationship_kind},
    },
    "provenance": {
        "required": {"node_id": _is_nonempty_string},
        "optional": {},
    },
    "inspect": {
        "required": {},
        "optional": {},
    },
}

DEFAULT_OPERATION_ORDER = (
    "search", "get", "related", "follow", "provenance", "inspect",
)


def operations():
    """Names of the supported operations, in stable order."""
    return DEFAULT_OPERATION_ORDER


def validate_request(request):
    """Validate a tool request and return ``(operation, arguments)``.

    Raises:
        ToolRequestError      request is not a plain object or is malformed
        UnknownOperationError operation is not defined
        KnowledgeArgumentError  an argument is missing, wrong-typed, or unknown

    The returned ``arguments`` dict contains exactly the whitelisted,
    type-checked argument values (an empty dict when no arguments given).
    """
    if not isinstance(request, dict):
        raise ToolRequestError("tool request must be a JSON object")

    operation = request.get("operation")
    if not isinstance(operation, str):
        raise ToolRequestError("'operation' must be a string")
    if operation not in OPERATIONS:
        raise UnknownOperationError(operation)

    raw = request.get("arguments", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ToolRequestError("'arguments' must be a JSON object")

    spec = OPERATIONS[operation]
    known = set(spec["required"]) | set(spec["optional"])
    unknown = sorted(set(raw) - known)
    if unknown:
        raise KnowledgeArgumentError(
            "unexpected argument(s) %s for operation %r; allowed: %s"
            % (unknown, operation, sorted(known)))

    args = {}
    for name, validator in spec["required"].items():
        if name not in raw:
            raise KnowledgeArgumentError(
                "missing required argument %r for operation %r"
                % (name, operation))
        value = raw[name]
        if not validator(value):
            raise KnowledgeArgumentError(
                "invalid value for argument %r of operation %r: %r"
                % (name, operation, value))
        args[name] = value

    for name, validator in spec["optional"].items():
        if name in raw:
            value = raw[name]
            if not validator(value):
                if name == "relationship_type":
                    raise RelationshipTypeError(
                        value, VALID_RELATIONSHIP_KINDS)
                raise KnowledgeArgumentError(
                    "invalid value for argument %r of operation %r: %r"
                    % (name, operation, value))
            args[name] = value

    limit_key = spec.get("limit_key")
    if limit_key and args.get(limit_key) is not None:
        args[limit_key] = min(args[limit_key], MAX_RESULTS)

    return operation, args


# -- documented response limits --------------------------------------------

OPERATION_LIMITS = {
    "search": "defaults to %d results; any requested limit is validated as a "
              "positive integer and capped at %d. Full node payloads are "
              "returned; use 'get' for a single node." % (
                  DEFAULT_SEARCH_LIMIT, MAX_RESULTS),
    "get": "returns a single node or an error",
    "related": "limit validated as a positive integer, capped at %d" % MAX_RESULTS,
    "follow": "returns all matching edges for the node",
    "provenance": "returns a single provenance record or an error",
    "inspect": "returns aggregate counts, always bounded",
}