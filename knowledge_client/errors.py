"""SDK exception hierarchy, mapped from the stable tool-contract error codes.

A thin, developer-friendly Python client for the Knowledge Engine should
surface failures as clean, catchable exceptions -- never as raw envelopes the
caller has to parse by hand, and never as leaked tracebacks or engine
internals.

Error codes preserved from the contract (:mod:`api.contract`) so the mapping
is exact:

    invalid_request            InvalidRequestError
    unknown_operation          UnknownOperationError
    invalid_argument           InvalidArgumentError
    invalid_relationship_type  InvalidRelationshipTypeError
    node_not_found             NodeNotFoundError
    internal_error             InternalError

Transport-level failures raise :class:`TransportError` (client-side, codes
prefixed ``client:``); a non-conforming envelope raises
:class:`InvalidResponseError`. Unknown engine codes fall back to
:class:`KnowledgeError` so the SDK never dies on an unexpected code.
"""

__all__ = [
    "KnowledgeClientError",
    "KnowledgeError",
    "InvalidRequestError",
    "UnknownOperationError",
    "InvalidArgumentError",
    "InvalidRelationshipTypeError",
    "NodeNotFoundError",
    "InternalError",
    "TransportError",
    "InvalidResponseError",
    "exception_for_code",
]


class KnowledgeClientError(Exception):
    """Base class for every error raised by the SDK.

    Attributes:
        code: stable machine-readable code. Contract codes come straight
            from the error envelope; client-side codes are prefixed with
            ``client:``.
        message: human-readable description.
        operation: the operation that was requested, if known.
        details: any structured extras from the error envelope (for example
            ``node_id`` for a not-found error).
    """

    code = "client:error"

    def __init__(self, message, operation=None, details=None):
        super().__init__(message)
        self.message = message
        self.operation = operation
        self.details = details if details is not None else {}

    def __str__(self):
        return self.message

    def __repr__(self):
        return "%s(code=%r, message=%r, operation=%r)" % (
            type(self).__name__, self.code, self.message, self.operation)


class KnowledgeError(KnowledgeClientError):
    """A knowledge-domain failure reported by the engine (generic fallback)."""

    code = "knowledge_error"


class InvalidRequestError(KnowledgeError):
    """The request itself was malformed (not an object, empty line, ...)."""

    code = "invalid_request"


class UnknownOperationError(KnowledgeError):
    """The requested operation is not part of the contract."""

    code = "unknown_operation"


class InvalidArgumentError(KnowledgeError):
    """An argument is missing, wrong-typed, or out of range."""

    code = "invalid_argument"


class InvalidRelationshipTypeError(KnowledgeError):
    """``relationship_type`` is not a canonical relationship kind."""

    code = "invalid_relationship_type"


class NodeNotFoundError(KnowledgeError):
    """The requested node id does not exist in the knowledge database."""

    code = "node_not_found"

    @property
    def node_id(self):
        return self.details.get("node_id") if self.details else None


class InternalError(KnowledgeError):
    """Unexpected failure behind the contract boundary (no details leak)."""

    code = "internal_error"


class TransportError(KnowledgeClientError):
    """The transport could not deliver the request (I/O, dead process, ...)."""

    code = "client:transport"


class InvalidResponseError(TransportError):
    """The transport returned a non-conforming response envelope."""

    code = "client:invalid_response"


_CODE_TO_EXCEPTION = {
    error_class.code: error_class
    for error_class in (
        InvalidRequestError,
        UnknownOperationError,
        InvalidArgumentError,
        InvalidRelationshipTypeError,
        NodeNotFoundError,
        InternalError,
    )
}


def exception_for_code(code, message="", operation=None, details=None):
    """Build the SDK exception matching a contract error code.

    Unknown codes fall back to :class:`KnowledgeError`, keeping the mapping
    open-ended rather than raising an import- or mapping-time crash.
    """
    cls = _CODE_TO_EXCEPTION.get(code, KnowledgeError)
    return cls(message, operation=operation, details=details or {})