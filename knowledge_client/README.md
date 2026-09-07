# Knowledge Engine Python Client SDK

A thin, developer-friendly Python client for the Knowledge Engine's stable
tool-call contract, **Public Contract v1** (see `docs/public-api-v1.md`). It is
intentionally small:

* builds contract-shaped requests for the six approved operations
  (`search`, `get`, `related`, `follow`, `provenance`, `inspect`);
* maps stable error codes onto clean, catchable SDK exceptions;
* depends on a single transport abstraction (`execute(request) -> response`);
* never imports or reaches `sqlite3`, the repository, the schema, or the CLI;
* returns the engine's plain, JSON-serializable dicts/lists unchanged
  (no custom object graph, no copied data).

> **Note:** this package also ships a v2 client. `MemoryClient`
> (`knowledge_client.memory_client`) issues the v2 operations (`remember`,
> `recall`, `get`, `provenance`, `inspect`, `context.get`, `lifecycle.*`)
> against Public Contract v2 (see `docs/public-contract.md`) via the v2
> transports (`MemoryInProcessTransport`, `MemorySessionTransport`,
> `HttpMemoryTransport`). The v1 `KnowledgeClient` below is frozen and
> unchanged.

## Install / import

Stdlib only — no third-party dependencies. Add this repository to your path
and import:

```python
from knowledge_client import KnowledgeClient, SessionTransport
```

The SDK is not yet published to a package index; import it from the checkout.

## Contract versioning

The SDK speaks contract version `1` (`knowledge_client.CONTRACT_VERSION`).
Every tool response envelope also carries `"contract_version": "1"`. The v1
contract is frozen: breaking changes would become a future v2, so code written
against this SDK keeps working. See `docs/public-api-v1.md` for the full
versioning policy and specification.

## Quick start

Persistent local session (engine loads the database once):

```python
from knowledge_client import KnowledgeClient, SessionTransport

client = KnowledgeClient(SessionTransport())

node = client.get("exceptions")
print(node["name"], node["type"])

hits = client.search("exception", limit=5)
node = client.get(hits[0]["id"])
provenance = client.provenance(node["id"])

neighbours = client.related(node["id"])
edges = client.follow(node["id"], relationship_type="extends")

stats = client.inspect()
client.close()
```

`SessionTransport` also works as a context manager (clean EOF shutdown when
the block exits):

```python
with SessionTransport() as session:
    client = KnowledgeClient(session)
    print(client.inspect())
```

## Supported operations

| SDK method | Contract operation | Purpose |
| --- | --- | --- |
| `search(query, limit=20, node_type=None)` | `search` | full-text search |
| `get(node_id)` | `get` | one node by id |
| `related(node_id, limit=None)` | `related` | neighbours either direction |
| `follow(node_id, relationship_type=None)` | `follow` | traverse relationships |
| `provenance(node_id)` | `provenance` | source record for a node |
| `inspect()` | `inspect` | aggregate counts |

Semantics (argument validation, limits, ordering, read-only) are defined by
the contract; the SDK performs no duplicated logic.

## Transports

The client only depends on the transport protocol — an object with
`execute(request) -> response` and `close()`. Three are provided:

| Transport | Boundary | Cost model |
| --- | --- | --- |
| `SessionTransport` | `python -m api.session` subprocess | repository loaded once; many requests per process |
| `InProcessTransport` | same process, `api.tools.ToolInterface` | cheapest; for embedded use and tests |
| `OneShotTransport` | `python -m api.tools -` subprocess | one fresh interpreter per request |

In-process example (e.g. inside a test):

```python
from knowledge_client import KnowledgeClient, InProcessTransport

client = KnowledgeClient(InProcessTransport(db_path="database/knowledge.db"))
print(client.inspect())
client.close()
```

Bring your own transport by implementing the protocol:

```python
class MyTransport:
    def execute(self, request: dict) -> dict:
        ...
    def close(self) -> None:
        ...
```

## Errors

Every engine rejection and transport failure raises an exception subclassing
`KnowledgeClientError` (never a raw envelope, never a traceback):

| Contract code | SDK exception |
| --- | --- |
| `invalid_request` | `InvalidRequestError` |
| `unknown_operation` | `UnknownOperationError` |
| `invalid_argument` | `InvalidArgumentError` |
| `invalid_relationship_type` | `InvalidRelationshipTypeError` |
| `node_not_found` | `NodeNotFoundError` |
| `internal_error` | `InternalError` |
| transport failure | `TransportError` |
| non-conforming envelope | `InvalidResponseError` |

All exceptions expose `.code`, `.message`, `.operation`, and `.details`:

```python
try:
    client.get("no-such-node")
except NodeNotFoundError as exc:
    print(exc.code)        # "node_not_found"
    print(exc.node_id)     # "no-such-node"
```

## Transports

The client only depends on the transport protocol — an object with
`execute(request) -> response` and `close()`. Three are provided:

| Transport             | Boundary | Cost model |
| --------------------- | -------- | ---------- |
| `SessionTransport`    | `python -m api.session` subprocess | repository loaded once; many requests per process |
| `InProcessTransport`  | same process, `api.tools.ToolInterface` | cheapest; for embedded use and tests |
| `OneShotTransport`    | `python -m api.tools -` subprocess | one fresh interpreter per request |

In-process example (e.g. inside a test):

```python
from knowledge_client import KnowledgeClient, InProcessTransport

client = KnowledgeClient(InProcessTransport(db_path="database/knowledge.db"))
print(client.inspect())
client.close()
```

Bring your own transport by implementing the protocol:

```python
class MyTransport:
    def execute(self, request: dict) -> dict:
        ...
    def close(self) -> None:
        ...
```

## Errors

Every engine rejection and transport failure raises an exception subclassing
`KnowledgeClientError` (never a raw envelope, never a traceback):

| Contract code | SDK exception |
| --- | --- |
| `invalid_request` | `InvalidRequestError` |
| `unknown_operation` | `UnknownOperationError` |
| `invalid_argument` | `InvalidArgumentError` |
| `invalid_relationship_type` | `InvalidRelationshipTypeError` |
| `node_not_found` | `NodeNotFoundError` |
| `internal_error` | `InternalError` |
| transport failure | `TransportError` |
| non-conforming envelope | `InvalidResponseError` |

All exceptions expose `.code`, `.message`, `.operation`, and `.details`:

```python
try:
    client.get("no-such-node")
except NodeNotFoundError as exc:
    print(exc.code)        # "node_not_found"
    print(exc.node_id)     # "no-such-node"
```

## Design rules

* The SDK never duplicates database, repository, search, traversal, or
  validation logic — the engine validates every request. Requests carry
  exactly the approved argument names and values.
* The SDK never accesses SQLite. Even the in-process transport goes through
  the public `api.tools.ToolInterface` boundary.
* The SDK does not fabricate node ids or results; it returns whatever the
  engine says exists.
* The SDK speaks one documented contract version (`CONTRACT_VERSION`) and does
  not silently change behavior across releases.