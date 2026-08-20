"""Knowledge API -- deterministic, read-only programmatic interface.

The API is the stable gateway to the knowledge engine. It wraps the existing
``KnowledgeRepository`` / ``KnowledgeStore`` (SQLite) and exposes search, get,
related, follow, inspect and provenance. The CLI is only one client of this API.
"""

from api.knowledge_api import KnowledgeAPI
from api.errors import (
    KnowledgeError,
    NodeNotFoundError,
    KnowledgeArgumentError,
    RelationshipTypeError,
    UnknownOperationError,
    ToolRequestError,
    InternalError,
)

__all__ = [
    "KnowledgeAPI",
    "KnowledgeError",
    "NodeNotFoundError",
    "KnowledgeArgumentError",
    "RelationshipTypeError",
    "UnknownOperationError",
    "ToolRequestError",
    "InternalError",
]