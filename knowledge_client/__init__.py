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

Memory (v2) use::

    from knowledge_client import MemoryClient, MemoryInProcessTransport

    client = MemoryClient(MemoryInProcessTransport(data_root="/tmp/data"))
    client.remember(text="hello", project_id="default")
"""

from knowledge_client.client import KnowledgeClient
from knowledge_client.memory_client import MemoryClient
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
    HttpMemoryTransport,
    HttpTransport,
    InProcessTransport,
    MemoryInProcessTransport,
    MemorySessionTransport,
    OneShotTransport,
    SessionTransport,
    TransportProtocol,
)

# Version of the public tool-call contract this SDK speaks. Mirrors the
# engine's stable CONTRACT_VERSION ("1"); the value is also echoed verbatim
# on every tool response envelope as ``contract_version``.
CONTRACT_VERSION = "1"
CONTRACT_VERSION_V2 = "2"

__version__ = "1.0.0"

__all__ = [
    "KnowledgeClient",
    "MemoryClient",
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
    "MemoryInProcessTransport",
    "MemorySessionTransport",
    "SessionTransport",
    "OneShotTransport",
    "HttpTransport",
    "HttpMemoryTransport",
    "CONTRACT_VERSION",
    "CONTRACT_VERSION_V2",
]