"""Knowledge Engine Python SDK client.

A thin, developer-friendly client over the tool-call contract. It builds the
request framing and maps stable contract error codes onto clean SDK
exceptions (:mod:`knowledge_client.errors`). It does NOT re-implement any
database, repository, search, traversal, or validation logic: every request
is sent to the transport and validated by the engine.

The client depends only on the transport abstraction
(:class:`~knowledge_client.transports.TransportProtocol`):
``execute(request) -> response``. Pass any of the provided transports
(in-process, persistent session, one-shot) or your own.
"""

from typing import Any, Dict, List, Optional

from knowledge_client.errors import (
    InvalidResponseError,
    KnowledgeClientError,
    TransportError,
    exception_for_code,
)
from knowledge_client.transports import TransportProtocol
from knowledge_client.types import (
    FollowEdge,
    InspectStats,
    Node,
    Provenance,
    RelatedEntry,
)

__all__ = [
    "KnowledgeClient",
    "Request",
    "Response",
]

# The contract request/response envelope shape.
Request = Dict[str, Any]
Response = Dict[str, Any]


class KnowledgeClient:
    """Contract-only client over an injected transport.

    Example::

        from knowledge_client import KnowledgeClient, SessionTransport

        client = KnowledgeClient(SessionTransport())
        node = client.search("exception", limit=5)
        details = client.get(node[0]["id"])
        client.close()

    Convenience ``with`` usage::

        with SessionTransport() as session:
            client = KnowledgeClient(session)
            print(client.inspect())
    """

    def __init__(self, transport):
        if not hasattr(transport, "execute"):
            raise TypeError(
                "transport must expose execute(request) -> response; "
                "got %r" % (type(transport).__name__,))
        self._transport = transport

    @property
    def transport(self):
        """The underlying transport (for low-level access and stats)."""
        return self._transport

    # -- low-level ----------------------------------------------------------

    def execute(self, request: Request) -> Response:
        """Send a raw framed request; return the raw response dict.

        Unlike the typed helpers, this never raises on an error envelope --
        useful for exercising error paths deliberately. It DOES raise
        :class:`TransportError` / :class:`InvalidResponseError` when the
        transport itself fails or returns garbage.
        """
        try:
            response = self._transport.execute(request)
        except KnowledgeClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep SDK exceptions clean
            raise TransportError("transport failed: %s" % (exc,)) from exc
        if not isinstance(response, dict) or not isinstance(
                response.get("ok"), bool):
            raise InvalidResponseError(
                "transport returned a non-conforming envelope: %r" % (response,))
        return response

    def request(self, operation: str, arguments: Optional[Dict[str, Any]] = None
                ) -> Any:
        """Send a framed request; return the result or raise an SDK exception.

        Any error envelope is mapped to the matching exception from
        :mod:`knowledge_client.errors` (``node_not_found`` becomes
        :class:`NodeNotFoundError`, ...).
        """
        request: Request = {"operation": operation}
        if arguments is not None:
            request["arguments"] = arguments
        response = self.execute(request)
        if not response["ok"]:
            error = response.get("error")
            if not isinstance(error, dict):
                raise InvalidResponseError(
                    "error envelope missing structured 'error' detail",
                    operation=response.get("operation"))
            raise exception_for_code(
                error.get("code", "knowledge_error"),
                error.get("message", ""),
                operation=error.get("operation", response.get("operation")),
                details={k: v for k, v in error.items()
                         if k not in ("code", "message")},
            )
        if "result" not in response:
            raise InvalidResponseError(
                "ok envelope missing 'result'", operation=response.get("operation"))
        return response["result"]

    # -- approved operations ------------------------------------------------

    def inspect(self) -> InspectStats:
        """Aggregate database facts (counts by type)."""
        return self.request("inspect")

    def search(self, query: str, limit: Optional[int] = 20,
               node_type: Optional[str] = None) -> List[Node]:
        """Full-text search over node text.

        ``limit`` defaults to the engine's default (20); ``node_type`` is an
        optional type filter. Returns a list of node dicts.
        """
        arguments: Dict[str, Any] = {"query": query}
        if limit is not None:
            arguments["limit"] = limit
        if node_type is not None:
            arguments["node_type"] = node_type
        return self.request("search", arguments)

    def get(self, node_id: str) -> Node:
        """Fetch one node by its id."""
        return self.request("get", {"node_id": node_id})

    def related(self, node_id: str, limit: Optional[int] = None) -> List[RelatedEntry]:
        """Neighbours of a node in either direction."""
        arguments: Dict[str, Any] = {"node_id": node_id}
        if limit is not None:
            arguments["limit"] = limit
        return self.request("related", arguments)

    def follow(self, node_id: str, relationship_type: Optional[str] = None
               ) -> List[FollowEdge]:
        """Follow edges of one type out of a node."""
        arguments: Dict[str, Any] = {"node_id": node_id}
        if relationship_type is not None:
            arguments["relationship_type"] = relationship_type
        return self.request("follow", arguments)

    def provenance(self, node_id: str) -> Provenance:
        """Provenance record for one node."""
        return self.request("provenance", {"node_id": node_id})

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Close the underlying transport (session EOF, interface release)."""
        closer = getattr(self._transport, "close", None)
        if closer is not None:
            closer()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False