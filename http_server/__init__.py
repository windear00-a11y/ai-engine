"""HTTP Transport v1 -- expose the frozen Knowledge Engine Public Contract v1.

HTTP is ONLY a transport layer. It maps HTTP requests onto the existing,
already-validated tool-call contract (:mod:`api.tools.ToolInterface`) and
preserves the contract response envelope verbatim, including
``contract_version``. No knowledge logic, database access, or validation is
introduced here; the contract stays the single source of truth.

Run::

    python -m http_server [--host 127.0.0.1] [--port 8765] [--db database/knowledge.db]
"""

from http_server.server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_STATUS,
    HTTP_STATUS_FOR_CODE,
    MAX_BODY_BYTES,
    V1Handler,
    KnowledgeHTTPServer,
    _single_env,
)

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_STATUS",
    "HTTP_STATUS_FOR_CODE",
    "MAX_BODY_BYTES",
    "V1Handler",
    "KnowledgeHTTPServer",
    "_single_env",
]