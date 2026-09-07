"""Memory Engine Public Contract v2 (additive, transport-independent).

This module implements Public Contract v2 for the generic Persistent
Intelligence / Memory layer. It is ADDITIVE and does NOT modify v1:

    v1 (CONTRACT_VERSION = "1") — frozen, 6 ops: search/get/related/follow/provenance/inspect
    v2 (CONTRACT_VERSION = "2") — generic Memory: remember/recall/get/provenance/inspect/context.get

v2 is transport-independent like v1: same envelope, same error codes,
same validation style, but operations are for Memory (per-project, validated,
deterministic). No network, no LLM, no filesystem paths, no SQL.

Request format (v2 — generic):
    {"operation": "remember", "arguments": {"payload": {"text": "hello"}, "project_id": "default"}}
    {"operation": "remember", "arguments": {"payload": {"custom": 42}, "context_hints": {"actor": {"user_id": "alice"}}}}
    {"operation": "recall",   "arguments": {"query": "hello", "limit": 20}}

Success:
    {"ok": true, "operation": "remember", "contract_version": "2", "result": {...}}
Error:
    {"ok": false, "operation": "remember", "contract_version": "2",
     "error": {"code": "invalid_argument", "message": "..."}}

Error codes are same as v1 (stable).
"""

import re

from api.errors import (
    KnowledgeArgumentError,
    ToolRequestError,
    UnknownOperationError,
)

# Stability marker for v2
CONTRACT_VERSION = "2"

# Validators
_PROJECT_RE = re.compile(r"^[a-z0-9_-]{1,64}$")

def _is_string(v):
    return isinstance(v, str)

def _is_nonempty_string(v):
    return isinstance(v, str) and bool(v.strip())

def _is_positive_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v > 0

def _is_project_id(v):
    return isinstance(v, str) and bool(_PROJECT_RE.match(v))

def _is_dict(v):
    return isinstance(v, dict)

def _is_list(v):
    return isinstance(v, list)

def _is_string_or_none(v):
    return v is None or isinstance(v, str)

def _is_nonempty_string_list(v):
    return (isinstance(v, list) and bool(v)
            and all(isinstance(x, str) and x.strip() for x in v))

# Operation specifications: required and optional validators
# For remember we use generic payload (arbitrary structured memory payload).
# Detailed type/relationship/vocabulary checks are done by Memory (which returns invalid_argument).
# Generic form (approved): {payload: {}, context_hints?: {}, project_id?: string}
OPERATIONS = {
    "remember": {
        "required": {"payload": _is_dict},
        "optional": {
            "context_hints": _is_dict,
            "project_id": _is_project_id,
        },
    },
    "recall": {
        "required": {"query": _is_nonempty_string},
        "optional": {
            "limit": _is_positive_int,
            "candidate_limit": _is_positive_int,
            "project_id": _is_project_id,
            "vocabulary_id": _is_string,
            "context": _is_dict,
        },
        "limit_key": "limit",
    },
    "get": {
        "required": {"node_id": _is_nonempty_string},
        "optional": {
            "project_id": _is_project_id,
            "vocabulary_id": _is_string,
        },
    },
    "provenance": {
        "required": {"node_id": _is_nonempty_string},
        "optional": {
            "project_id": _is_project_id,
            "vocabulary_id": _is_string,
        },
    },
    "inspect": {
        "required": {},
        "optional": {
            "project_id": _is_project_id,
            "vocabulary_id": _is_string,
        },
    },
    "context.get": {
        "required": {"context_id": _is_nonempty_string},
        "optional": {
            "project_id": _is_project_id,
        },
    },
    # -- Phase 26: canonical information / knowledge / experience lifecycle --
    "lifecycle.ingest": {
        "required": {"content": _is_nonempty_string},
        "optional": {
            "origin": _is_string_or_none,
            "source": _is_string_or_none,
            "uri": _is_string_or_none,
            "role": _is_string_or_none,
            "actor": _is_string_or_none,
            "project_id": _is_project_id,
            "context_hints": _is_dict,
        },
    },
    "lifecycle.experience": {
        "required": {
            "situation": _is_nonempty_string,
            "attempt": _is_nonempty_string,
            "result": _is_string,
        },
        "optional": {
            "context_id": _is_string_or_none,
            "evidence_ids": _is_list,
            "task_id": _is_string_or_none,
            "outcome_classification": _is_string_or_none,
            "outcome_id": _is_string_or_none,
            "task_type": _is_string_or_none,
            "domain": _is_string_or_none,
            "strategy_id": _is_string_or_none,
            "actor": _is_string_or_none,
            "source": _is_string_or_none,
            "project_id": _is_project_id,
        },
    },
    "lifecycle.learning": {
        "required": {"experience_ids": _is_list},
        "optional": {
            "project_id": _is_project_id,
        },
    },
    "lifecycle.strategy": {
        "required": {"experience_ids": _is_list},
        "optional": {
            "project_id": _is_project_id,
            "min_samples": _is_positive_int,
        },
    },
    "lifecycle.trace": {
        "required": {"record_id": _is_nonempty_string},
        "optional": {
            "role": _is_string_or_none,
            "project_id": _is_project_id,
            "max_depth": _is_positive_int,
        },
    },
    "lifecycle.describe": {
        "required": {"record_id": _is_nonempty_string},
        "optional": {
            "role": _is_string_or_none,
            "project_id": _is_project_id,
        },
    },
    "lifecycle.summary": {
        "required": {},
        "optional": {
            "project_id": _is_project_id,
        },
    },
    "lifecycle.plan": {
        "required": {
            "situation": _is_nonempty_string,
            "experience_ids": _is_list,
        },
        "optional": {
            "context_id": _is_string_or_none,
            "constraints": _is_dict,
            "max_steps": _is_positive_int,
            "min_samples": _is_positive_int,
            "project_id": _is_project_id,
        },
    },
    # -- Phase 29: safe action / observation / verification loop --
    "lifecycle.grant": {
        "required": {
            "plan_id": _is_nonempty_string,
            "plan_step_ids": _is_nonempty_string_list,
            "actor": _is_nonempty_string,
        },
        "optional": {
            "mechanism": _is_string_or_none,
            "evidence_ids": _is_list,
            "project_id": _is_project_id,
        },
    },
    "lifecycle.authorize": {
        "required": {
            "plan_id": _is_nonempty_string,
            "plan_step_ids": _is_nonempty_string_list,
            "actor": _is_nonempty_string,
        },
        "optional": {
            "policy": _is_dict,
            "request_ref": _is_string_or_none,
            "project_id": _is_project_id,
        },
    },
    "lifecycle.execute": {
        "required": {
            "plan_id": _is_nonempty_string,
            "plan_step_id": _is_nonempty_string,
            "actor": _is_nonempty_string,
        },
        "optional": {
            "request_id": _is_string_or_none,
            "policy": _is_dict,
            "executors": _is_dict,
            "project_id": _is_project_id,
        },
    },
}

DEFAULT_OPERATION_ORDER = (
    "remember", "recall", "get", "provenance", "inspect", "context.get",
    "lifecycle.ingest", "lifecycle.experience", "lifecycle.learning",
    "lifecycle.strategy", "lifecycle.trace", "lifecycle.describe",
    "lifecycle.summary", "lifecycle.plan", "lifecycle.grant",
    "lifecycle.authorize", "lifecycle.execute",
)

MAX_RESULTS = 100
DEFAULT_SEARCH_LIMIT = 20


def operations():
    return DEFAULT_OPERATION_ORDER


def validate_request(request):
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
                "missing required argument %r for operation %r" % (name, operation))
        value = raw[name]
        if not validator(value):
            raise KnowledgeArgumentError(
                "invalid value for argument %r of operation %r: %r" % (name, operation, value))
        args[name] = value

    for name, validator in spec["optional"].items():
        if name in raw:
            value = raw[name]
            if not validator(value):
                raise KnowledgeArgumentError(
                    "invalid value for argument %r of operation %r: %r" % (name, operation, value))
            args[name] = value

    # Normalize project_id default handling is done by caller; here just cap limits
    limit_key = spec.get("limit_key")
    if limit_key and args.get(limit_key) is not None:
        args[limit_key] = min(args[limit_key], MAX_RESULTS)

    return operation, args
