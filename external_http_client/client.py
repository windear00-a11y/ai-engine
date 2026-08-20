"""Independent external HTTP client for the Knowledge Engine Public Contract v1.

This module knows ONLY the public request/response contract defined in
``docs/public-api-v1.md`` (operation names, argument names, response
envelope shapes).  It communicates exclusively through HTTP via
:class:`~external_http_client.transport.HTTPTransport`.

It does NOT import, reach into, or depend on:

* ``sqlite3`` or any SQL
* ``retrieval.*`` / ``KnowledgeRepository``
* ``api.knowledge_api`` / ``api.contract`` / ``api.tools``
* ``http_server`` internals
* the CLI (``ai_engine``)

It uses only the Python standard library (``http.client``, ``json``,
``time``, ``threading``, ``typing``).
"""

from typing import Any, Dict, List, Optional

from external_http_client.errors import (
    InvalidResponseError,
    TransportError,
    exception_for_code,
)
from external_http_client.transport import HTTPTransport
from external_http_client.types import (
    FollowEdge,
    InspectStats,
    Node,
    Provenance,
    RelatedEntry,
)

__all__ = [
    "ExternalHTTPClient",
    "HTTPTransport",
    "CONTRACT_VERSION",
]

# Version of the public tool-call contract this client speaks.
# Must match the engine's stable CONTRACT_VERSION ("1").
CONTRACT_VERSION = "1"


class ExternalHTTPClient:
    """Contract-only client over an injected HTTP transport.

    Every request is sent as a JSON POST to ``/v1/execute`` and the
    response envelope (including ``contract_version``) is returned verbatim.
    The typed helpers raise clean, catchable exceptions for error envelopes
    rather than requiring callers to inspect ``ok`` / ``error`` by hand.

    Parameters
    ----------
    transport : optional; an :class:`HTTPTransport` instance.  When ``None``
        one is created with the given ``host``, ``port``, ``api_key``, and
        ``timeout`` arguments.
    host, port, api_key, timeout : forwarded to :class:`HTTPTransport` when
        ``transport`` is ``None``.
    """

    def __init__(self, transport=None, host=None, port=None,
                 api_key=None, timeout=None):
        if transport is not None:
            self._transport = transport
        else:
            kwargs = {}
            if host is not None:
                kwargs["host"] = host
            if port is not None:
                kwargs["port"] = port
            if api_key is not None:
                kwargs["api_key"] = api_key
            if timeout is not None:
                kwargs["timeout"] = timeout
            self._transport = HTTPTransport(**kwargs)

    @property
    def transport(self):
        """The underlying HTTP transport."""
        return self._transport

    # -- low-level ----------------------------------------------------------

    def execute(self, request):
        """Send a raw framed request; return the raw response dict.

        Unlike the typed helpers, this never raises on an ``ok: false``
        envelope -- useful for exercising error paths deliberately.  It
        DOES raise :class:`~external_http_client.errors.TransportError` /
        :class:`~external_http_client.errors.InvalidResponseError` when the
        transport itself fails or returns garbage.
        """
        envelope = self._transport.execute(request)
        # Validate contract_version when present.
        cv = envelope.get("contract_version")
        if cv is not None and str(cv) != CONTRACT_VERSION:
            raise InvalidResponseError(
                "server reported contract_version %r; expected %r"
                % (cv, CONTRACT_VERSION))
        return envelope

    def request(self, operation, arguments=None):
        """Send a framed request; return ``result`` or raise a mapped exception.

        Any error envelope is mapped to the matching exception from
        :mod:`external_http_client.errors` (``node_not_found`` becomes
        :class:`~external_http_client.errors.NodeNotFoundError`, ...).
        """
        request = {"operation": operation}
        if arguments is not None:
            request["arguments"] = arguments
        envelope = self.execute(request)
        if not envelope["ok"]:
            error = envelope.get("error")
            if not isinstance(error, dict):
                raise InvalidResponseError(
                    "error envelope missing structured 'error' detail",
                    operation=envelope.get("operation"),
                    http_status=envelope.get("_http_status"))
            raise exception_for_code(
                error.get("code", "knowledge_error"),
                error.get("message", ""),
                operation=error.get("operation", envelope.get("operation")),
                http_status=envelope.get("_http_status"),
                details={k: v for k, v in error.items()
                         if k not in ("code", "message")},
            )
        if "result" not in envelope:
            raise InvalidResponseError(
                "ok envelope missing 'result'",
                operation=envelope.get("operation"),
                http_status=envelope.get("_http_status"))
        return envelope["result"]

    # -- typed helpers (approved operations only) ---------------------------

    def inspect(self) -> InspectStats:
        """Aggregate database facts (counts by type)."""
        return self.request("inspect")

    def search(self, query: str, limit: Optional[int] = 20,
               node_type: Optional[str] = None) -> List[Node]:
        """Full-text search over node text; returns a list of nodes."""
        arguments: Dict[str, Any] = {"query": query}
        if limit is not None:
            arguments["limit"] = limit
        if node_type is not None:
            arguments["node_type"] = node_type
        return self.request("search", arguments)

    def get(self, node_id: str) -> Node:
        """Fetch one node by its id."""
        return self.request("get", {"node_id": node_id})

    def related(self, node_id: str, limit: Optional[int] = None
                ) -> List[RelatedEntry]:
        """Neighbours of a node in either direction."""
        arguments: Dict[str, Any] = {"node_id": node_id}
        if limit is not None:
            arguments["limit"] = limit
        return self.request("related", arguments)

    def follow(self, node_id: str,
               relationship_type: Optional[str] = None) -> List[FollowEdge]:
        """Follow edges of one type out of a node."""
        arguments: Dict[str, Any] = {"node_id": node_id}
        if relationship_type is not None:
            arguments["relationship_type"] = relationship_type
        return self.request("follow", arguments)

    def provenance(self, node_id: str) -> Provenance:
        """Provenance record for one node."""
        return self.request("provenance", {"node_id": node_id})

    def health(self):
        """GET /health (not part of the contract; transport-level check)."""
        return self._transport.health()

    # -- lifecycle ----------------------------------------------------------

    def close(self):
        """Close the underlying HTTP transport."""
        self._transport.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False
