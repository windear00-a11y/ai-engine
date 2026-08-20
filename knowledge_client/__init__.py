"""Knowledge Engine Python Client SDK.

A thin, transport-based Python client for the Knowledge Engine tool-call
contract. It builds request framing, maps stable error codes onto clean SDK
exceptions, and never touches SQLite, the repository, or the schema.

Typical use::

    from knowledge_client import KnowledgeClient, SessionTransport

    client = KnowledgeClient(SessionTransport())
    node = client.get("exceptions")
    print(node["name"])
    client.close()
"""

from knowledge_client.client import KnowledgeClient
from knowledge_client.errors import (
    InternalError,
    InvalidArgumentError,
    InvalidRelationshipTypeError,
    InvalidRequestError,
    InvalidResponseError,
    KnowledgeClientError,
    KnowledgeError,
    NodeNotFoundError,
    TransportError,
    UnknownOperationError,
)
from knowledge_client.transports import (
    InProcessTransport,
    OneShotTransport,
    SessionTransport,
    TransportProtocol,
)

__version__ = "1.0.0"

__all__ = [
    "KnowledgeClient",
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
    "TransportProtocol",
    "InProcessTransport",
    "SessionTransport",
    "OneShotTransport",
]