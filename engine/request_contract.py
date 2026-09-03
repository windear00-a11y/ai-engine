"""Request → StructuredIntent contract (AI Engine v1).

Separate from Contract v1 (api/contract.py). Validates the user-facing
`request` envelope before it reaches the adapter. No execution, no DB.
"""

from engine.request_adapter import ALLOWED_INTENTS

MAX_REQUEST_LEN = 4000
MIN_REQUEST_LEN = 1
MAX_WORKSPACE_LEN = 1024

def _is_string(v):
    return isinstance(v, str)

def _is_nonempty_string(v):
    return isinstance(v, str) and 1 <= len(v.strip()) <= MAX_REQUEST_LEN

def _is_optional_string(v):
    return v is None or isinstance(v, str)

def _is_positive_int(v):
    return isinstance(v, int) and not isinstance(v, bool) and v > 0

def _is_constraints(v):
    if not isinstance(v, dict):
        return False
    allowed = {"allow_write", "max_steps", "max_duration"}
    if any(k not in allowed for k in v):
        return False
    if "allow_write" in v and not isinstance(v["allow_write"], bool):
        return False
    if "max_steps" in v and not _is_positive_int(v["max_steps"]):
        return False
    if "max_duration" in v and not _is_positive_int(v["max_duration"]):
        return False
    # Clamp ranges checked in handler, not rejected here except type
    return True

# Single operation for v1: request.execute
OPERATIONS = {
    "request.execute": {
        "required": {"request": _is_nonempty_string},
        "optional": {
            "workspace_root": _is_optional_string,
            "constraints": _is_constraints,
            "request_id": _is_nonempty_string,
        },
    }
}

# Explicitly forbidden client authority smuggling keys
FORBIDDEN_KEYS = {"approved", "operator_approval", "approval", "approved_true", "bypass", "allow_write_true"}

class RequestValidationError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message

def validate_request(request):
    if not isinstance(request, dict):
        raise RequestValidationError("invalid_request", "request must be a JSON object")
    operation = request.get("operation", "request.execute")
    # Allow implicit operation if only "request" provided (transport convenience)
    if "operation" in request and operation != "request.execute":
        raise RequestValidationError("unknown_operation", f"unknown operation {operation!r}")
    raw = request.get("arguments", request)
    # Support both {request, workspace_root} and {arguments:{request,...}} forms
    if "arguments" in request and isinstance(request["arguments"], dict):
        raw = request["arguments"]
        operation = request.get("operation", "request.execute")
    elif "operation" in request:
        raw = request.get("arguments", {})
        if raw is None:
            raw = {}
    else:
        # Direct form: {request: "...", workspace_root: "..."}
        raw = {k: v for k, v in request.items() if k not in ("operation",)}
        operation = "request.execute"
    if not isinstance(raw, dict):
        raise RequestValidationError("invalid_request", "'arguments' must be a JSON object")
    # Check forbidden keys (prevent approved=true bypass)
    for k in list(raw.keys()):
        if k in FORBIDDEN_KEYS:
            raise RequestValidationError("invalid_argument", f"forbidden key {k!r} — approval is server-side only")
    spec = OPERATIONS["request.execute"]
    known = set(spec["required"]) | set(spec["optional"])
    unknown = sorted(set(raw) - known)
    if unknown:
        raise RequestValidationError("invalid_argument", f"unexpected argument(s) {unknown} for request.execute")
    args = {}
    for name, validator in spec["required"].items():
        if name not in raw:
            raise RequestValidationError("invalid_argument", f"missing required argument {name!r}")
        value = raw[name]
        if not validator(value):
            raise RequestValidationError("invalid_argument", f"invalid value for {name!r}: {value!r}")
        args[name] = value
    for name, validator in spec["optional"].items():
        if name in raw:
            value = raw[name]
            if not validator(value):
                raise RequestValidationError("invalid_argument", f"invalid value for {name!r}: {value!r}")
            args[name] = value
    # Normalize request string
    args["request"] = args["request"].strip()
    if "workspace_root" in args and args["workspace_root"] is not None:
        args["workspace_root"] = args["workspace_root"].strip() or None
    return operation, args

def operations():
    return tuple(OPERATIONS.keys())
