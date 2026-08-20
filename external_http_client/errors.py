"""Standalone error hierarchy for the external HTTP client.

Maps the six frozen Contract v1 error codes onto clean, catchable exceptions.
The base class carries the stable ``code``, ``message``, ``operation``, and
optional ``http_status`` (populated by the HTTP transport when the server
returns a non-200 HTTP status alongside a conforming JSON envelope).

A transport-level ``AuthenticationError`` (code ``"unauthorized"``) is also
provided; this code is NOT part of the documented Contract v1 error set but is
emitted by the server's optional API-key gate at the HTTP transport boundary.
"""

__all__ = [
    "ExternalHTTPClientError",
    "KnowledgeError",
    "InvalidRequestError",
    "UnknownOperationError",
    "InvalidArgumentError",
    "InvalidRelationshipTypeError",
    "NodeNotFoundError",
    "InternalError",
    "TransportError",
    "InvalidResponseError",
    "AuthenticationError",
    "exception_for_code",
]


class ExternalHTTPClientError(Exception):
    """Base class for every error raised by the external HTTP client.

    Attributes:
        code: stable machine-readable code. Contract codes come from the error
            envelope; client-side codes are prefixed with ``client:``.
        message: human-readable description.
        operation: the operation that was requested, if known.
        http_status: HTTP status code from the server, if available.
        details: any structured extras from the error envelope.
    """

    code = "client:error"

    def __init__(self, message, operation=None, http_status=None, details=None):
        super().__init__(message)
        self.message = message
        self.operation = operation
        self.http_status = http_status
        self.details = details if details is not None else {}

    def __str__(self):
        return self.message

    def __repr__(self):
        return "%s(code=%r, message=%r, operation=%r, http_status=%r)" % (
            type(self).__name__, self.code, self.message,
            self.operation, self.http_status)


class KnowledgeError(ExternalHTTPClientError):
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


class InternalError(KnowledgeError):
    """Unexpected failure behind the contract boundary (no details leak)."""

    code = "internal_error"


class TransportError(ExternalHTTPClientError):
    """The transport could not deliver the request (I/O, connection, ...)."""

    code = "client:transport"


class InvalidResponseError(TransportError):
    """The transport returned a non-conforming response envelope."""

    code = "client:invalid_response"


class AuthenticationError(ExternalHTTPClientError):
    """The server rejected the request due to a missing or invalid API key.

    This is a transport-level gate; ``"unauthorized"`` is NOT one of the six
    frozen Contract v1 error codes but is emitted by the server's optional
    API-key gate at the HTTP transport boundary.
    """

    code = "unauthorized"


_CODE_TO_EXCEPTION = {
    error_class.code: error_class
    for error_class in (
        InvalidRequestError,
        UnknownOperationError,
        InvalidArgumentError,
        InvalidRelationshipTypeError,
        NodeNotFoundError,
        InternalError,
        AuthenticationError,
    )
}


def exception_for_code(code, message="", operation=None,
                       http_status=None, details=None):
    """Build the client exception matching a contract or transport error code.

    Unknown codes fall back to :class:`KnowledgeError`, keeping the mapping
    open-ended rather than raising an import- or mapping-time crash.
    """
    cls = _CODE_TO_EXCEPTION.get(code, KnowledgeError)
    return cls(message, operation=operation, http_status=http_status,
               details=details or {})
