"""Intelligence API contract (separate from Contract v1) (Phase 10).

Read-only API for intelligence inspection. No mutations.

Operations:
- experience.search  {task_type?, domain?, context_id?, limit?}
- experience.get     {experience_id}
- decision.get       {decision_id}
- decision.audit     {limit?}
- learning.events    {limit?}
- knowledge.confidence {node_id}
- knowledge.lifecycle  {node_id}
- context.get        {context_id}
- context.similar    {context_id, limit?}
"""

from .types import IntelligenceAPIError

INTELLIGENCE_CONTRACT_VERSION = "1"
MAX_RESULTS = 100
DEFAULT_LIMIT = 20


def _is_string(v):
    return isinstance(v, str)


def _is_nonempty_string(v):
    return isinstance(v, str) and bool(v.strip())


def _is_positive_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v > 0


def _is_optional_string(v):
    return v is None or isinstance(v, str)


OPERATIONS = {
    "experience.search": {
        "required": {},
        "optional": {
            "task_type": _is_optional_string,
            "domain": _is_optional_string,
            "context_id": _is_optional_string,
            "limit": _is_positive_int,
        },
        "limit_key": "limit",
    },
    "experience.get": {
        "required": {"experience_id": _is_nonempty_string},
        "optional": {},
    },
    "decision.get": {
        "required": {"decision_id": _is_nonempty_string},
        "optional": {},
    },
    "decision.audit": {
        "required": {},
        "optional": {"limit": _is_positive_int},
        "limit_key": "limit",
    },
    "learning.events": {
        "required": {},
        "optional": {"limit": _is_positive_int},
        "limit_key": "limit",
    },
    "knowledge.confidence": {
        "required": {"node_id": _is_nonempty_string},
        "optional": {},
    },
    "knowledge.lifecycle": {
        "required": {"node_id": _is_nonempty_string},
        "optional": {},
    },
    "context.get": {
        "required": {"context_id": _is_nonempty_string},
        "optional": {},
    },
    "context.similar": {
        "required": {"context_id": _is_nonempty_string},
        "optional": {"limit": _is_positive_int},
        "limit_key": "limit",
    },
}


def operations():
    return tuple(OPERATIONS.keys())


def validate_request(request):
    if not isinstance(request, dict):
        raise IntelligenceAPIError("invalid_request", "request must be a JSON object")
    operation = request.get("operation")
    if not isinstance(operation, str):
        raise IntelligenceAPIError("invalid_request", "'operation' must be a string")
    if operation not in OPERATIONS:
        raise IntelligenceAPIError("unknown_operation", f"unknown operation {operation!r}")
    raw = request.get("arguments", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise IntelligenceAPIError("invalid_request", "'arguments' must be a JSON object")
    spec = OPERATIONS[operation]
    known = set(spec["required"]) | set(spec["optional"])
    unknown = sorted(set(raw) - known)
    if unknown:
        raise IntelligenceAPIError("invalid_argument",
                                   f"unexpected arguments {unknown} for {operation!r}")
    args = {}
    for name, validator in spec["required"].items():
        if name not in raw:
            raise IntelligenceAPIError("invalid_argument", f"missing required argument {name!r}")
        value = raw[name]
        if not validator(value):
            raise IntelligenceAPIError("invalid_argument", f"invalid value for {name!r}: {value!r}")
        args[name] = value
    for name, validator in spec["optional"].items():
        if name in raw:
            value = raw[name]
            if not validator(value):
                raise IntelligenceAPIError("invalid_argument", f"invalid value for {name!r}: {value!r}")
            args[name] = value
    limit_key = spec.get("limit_key")
    if limit_key and args.get(limit_key) is not None:
        args[limit_key] = min(args[limit_key], MAX_RESULTS)
    return operation, args
