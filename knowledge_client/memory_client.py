"""Memory Engine Python SDK client (v2).

Thin client over Contract v2 (remember/recall etc.). Same transport
abstraction as KnowledgeClient, but operations are for Memory (per-project,
deterministic). Keeps v1 untouched.

Example:
    from knowledge_client.memory_client import MemoryClient
    from knowledge_client.transports import InProcessTransport  # or MemoryInProcess

    client = MemoryClient(transport)  # transport must handle v2 ops
    res = client.remember(text="hello", project_id="default")
    hits = client.recall(query="hello", project_id="default")
"""

from typing import Any, Dict, List, Optional

from knowledge_client.errors import (
    InvalidResponseError,
    KnowledgeClientError,
    TransportError,
    exception_for_code,
)
from knowledge_client.transports import TransportProtocol


class MemoryClient:
    """Contract v2 client over an injected transport.

    Transport must expose execute(request) -> response where request is
    {"operation": "remember", "arguments": {...}} and response is v2 envelope.

    For in-process testing, use MemoryInProcessTransport(data_root=...).
    """

    def __init__(self, transport):
        if not hasattr(transport, "execute"):
            raise TypeError("transport must expose execute(request) -> response")
        self._transport = transport

    @property
    def transport(self):
        return self._transport

    def execute(self, request):
        try:
            response = self._transport.execute(request)
        except KnowledgeClientError:
            raise
        except Exception as exc:
            raise TransportError("transport failed: %s" % exc) from exc
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise InvalidResponseError("transport returned non-conforming envelope: %r" % response)
        return response

    def request(self, operation, arguments=None):
        req = {"operation": operation}
        if arguments is not None:
            req["arguments"] = arguments
        resp = self.execute(req)
        if not resp["ok"]:
            err = resp.get("error")
            if not isinstance(err, dict):
                raise InvalidResponseError("error envelope missing 'error'", operation=resp.get("operation"))
            raise exception_for_code(
                err.get("code", "knowledge_error"),
                err.get("message", ""),
                operation=err.get("operation", resp.get("operation")),
                details={k: v for k, v in err.items() if k not in ("code", "message")},
            )
        if "result" not in resp:
            raise InvalidResponseError("ok envelope missing 'result'", operation=resp.get("operation"))
        return resp["result"]

    # -- v2 operations (spec, hardened, transport-independent) -----------
    # Exact spec: remember(payload, context_hints=None, project_id=None)
    def remember(self, payload, context_hints=None, project_id=None):
        # Generic payload (arbitrary structured memory payload) — keep client-side
        # validation minimal; server contract is authoritative.
        if not isinstance(payload, dict):
            from knowledge_client.errors import InvalidArgumentError
            raise InvalidArgumentError("payload must be a JSON object (dict)")
        args = {"payload": payload}
        if context_hints is not None:
            args["context_hints"] = context_hints
        if project_id is not None:
            args["project_id"] = project_id
        return self.request("remember", args)

    # Recall spec: query, limit, candidate_limit, context, project_id, vocabulary_id
    def recall(self, query, limit=20, project_id=None, context=None,
               candidate_limit=None, vocabulary_id=None, **kwargs):
        args = {"query": query, "limit": limit}
        if candidate_limit is not None:
            args["candidate_limit"] = candidate_limit
        if project_id is not None:
            args["project_id"] = project_id
        if context is not None:
            args["context"] = context
        if vocabulary_id is not None:
            args["vocabulary_id"] = vocabulary_id
        return self.request("recall", args)

    def get(self, node_id, project_id=None, vocabulary_id=None):
        args = {"node_id": node_id}
        if project_id is not None:
            args["project_id"] = project_id
        if vocabulary_id is not None:
            args["vocabulary_id"] = vocabulary_id
        return self.request("get", args)

    def provenance(self, node_id, project_id=None, vocabulary_id=None):
        args = {"node_id": node_id}
        if project_id is not None:
            args["project_id"] = project_id
        if vocabulary_id is not None:
            args["vocabulary_id"] = vocabulary_id
        return self.request("provenance", args)

    def inspect(self, project_id=None, vocabulary_id=None):
        args = {}
        if project_id is not None:
            args["project_id"] = project_id
        if vocabulary_id is not None:
            args["vocabulary_id"] = vocabulary_id
        return self.request("inspect", args)

    def context_get(self, context_id, project_id=None):
        args = {"context_id": context_id}
        if project_id is not None:
            args["project_id"] = project_id
        return self.request("context.get", args)

    def close(self):
        closer = getattr(self._transport, "close", None)
        if closer:
            try:
                closer()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
