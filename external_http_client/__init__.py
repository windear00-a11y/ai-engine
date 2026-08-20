"""Independent external HTTP client for the Knowledge Engine Public Contract v1.

A standalone, stdlib-only client that communicates with the Knowledge Engine
exclusively through HTTP.  It does not import ``sqlite3``, any ``retrieval``
module, ``api.*``, or ``http_server.*`` internals.

Typical use::

    from external_http_client import ExternalHTTPClient

    client = ExternalHTTPClient(host="127.0.0.1", port=8765)
    node = client.get("exceptions")
    print(node["name"])
    client.close()

Convenience ``with`` usage::

    with ExternalHTTPClient(host="127.0.0.1", port=8765) as client:
        print(client.inspect())
"""

from external_http_client.client import (
    CONTRACT_VERSION,
    ExternalHTTPClient,
)
from external_http_client.errors import (
    AuthenticationError,
    ExternalHTTPClientError,
    InvalidArgumentError,
    InvalidRelationshipTypeError,
    InvalidRequestError,
    InvalidResponseError,
    InternalError,
    KnowledgeError,
    NodeNotFoundError,
    TransportError,
    UnknownOperationError,
    exception_for_code,
)
from external_http_client.transport import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_TIMEOUT,
    HTTPTransport,
)

__version__ = "1.0.0"

__all__ = [
    "ExternalHTTPClient",
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
    "HTTPTransport",
    "CONTRACT_VERSION",
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "exception_for_code",
]

# Modules that this package MUST NOT import.
FORBIDDEN_MODULES = frozenset({
    "sqlite3", "retrieval", "knowledge_api", "http_server",
    "ai_engine", "schema", "api", "knowledge_compiler",
})
