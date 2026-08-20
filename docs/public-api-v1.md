# Knowledge Engine Public Contract v1

> **Version:** 1
> **Status:** stable / frozen
> **Scope:** transport-independent tool-call contract
> **Date:** 2026

This document is the authoritative specification of the **Knowledge Engine
Public Contract v1**. It defines everything an external program needs to talk
to the Knowledge Engine: the operations, their arguments, response shapes,
error codes, ordering guarantees, and read-only guarantees.

It deliberately says **nothing** about how the engine is implemented. The
contract is transport-independent: the same request/response document works
over a persistent process session, an in-process call, a one-shot subprocess,
or (in a later milestone) HTTP.

---

## 1. Contract stability and versioning

* Contract v1 is **frozen**. Existing operations and their meanings must not
  be silently changed.
* Every tool response (success **and** error) carries
  `"contract_version": "1"` on the envelope, so consumers can verify which
  contract they are speaking.
* **Breaking changes require a new major version (v2).** A future v2 may add,
  remove, or alter operations, arguments, or response shapes — but it must do
  so as a **separate, versioned contract** that does not silently change v1
  semantics. v1 consumers must keep receiving v1 behavior.
* **Additive, backward-compatible changes** (new optional arguments, new
  response fields) are allowed within v1 and are documented here.

Honoring this policy is code-enforced by the compatibility test suite
(`tests/test_contract_v1.py`).

---

## 2. Public vs internal

External users must never need to know how the engine works internally.

### PUBLIC (stable, documented here)
| Layer | Notes |
| --- | --- |
| Tool request / response contract | envelope + operations below |
| `KnowledgeClient` API (Python SDK) | `knowledge_client`; a thin wrapper over the same contract |
| Supported operations | `search`, `get`, `related`, `follow`, `provenance`, `inspect` |
| Stable error codes | listed below |
| CLI (`python -m ai_engine`) | one client of this contract (see `docs/cli.md`) |

### INTERNAL (never part of the public contract)
| Layer | Notes |
| --- | --- |
| SQLite | storage engine; no request may reference it |
| `KnowledgeRepository` / `KnowledgeStore` | repository internals |
| `retrieval.*` implementation | search/scoring internals |
| Knowledge Compiler internals | `knowledge_compiler` |
| acceptance internals | `acceptance` |
| Persistent session implementation | `api/session` process details (line framing, stats) |

The contract rejects any request that tries to smuggle internal knowledge
in: `sql`, `path`, and `command` argument names are **never** accepted for any
operation.

---

## 3. Message format

### Request (JSON object)

```json
{
  "operation": "search",
  "arguments": { "query": "exception", "limit": 10 }
}
```

* `operation`: required, string, one of the six operations.
* `arguments`: optional object. If present, every key must be an argument
  allowed for that operation (see below). Unknown keys are rejected.

### Success response (JSON object)

```json
{
  "ok": true,
  "operation": "search",
  "contract_version": "1",
  "result": [ ... ]
}
```

### Error response (JSON object, no stack traces)

```json
{
  "ok": false,
  "operation": "search",
  "contract_version": "1",
  "error": { "code": "invalid_argument", "message": "..." }
}
```

---

## 4. Operations

For each operation: required arguments, optional arguments, accepted types,
defaults, limits, success shape, error codes, ordering guarantee, and the
read-only guarantee.

**General rules:**
* All operations are **read-only**. None of them writes to the knowledge
  database.
* String arguments must be non-empty (stripped).
* `limit` must be a positive integer; it is capped at `100` results.
* `node_type` must be one of the canonical node types
  (`concept`, `technology`, `entity`, `procedure`, `rule`, `example`,
  `dependency`).
* `relationship_type` must be one of the canonical relationship kinds.

### 4.1 `search`

| Field | Required | Type | Default | Limits |
| --- | --- | --- | --- | --- |
| `query` | yes | string (non-empty) | — | — |
| `limit` | no | positive int | `20` | capped at `100` |
| `node_type` | no | canonical node type | all types | — |

Full-text search over node text (id, type, name, description, metadata).
Returns node payloads (with a `_score` ranking field) so callers can surface
ranking.

**Success shape:** a list of node objects.

**Ordering guarantee:** deterministic — by score descending, then node id
ascending.

**Read-only:** yes. Errors: `invalid_argument`.

### 4.2 `get`

| Field | Required | Type | Default | Limits |
| --- | --- | --- | --- | --- |
| `node_id` | yes | string (non-empty) | — | — |

Fetch a single node by id (type, metadata, relationships, provenance).

**Success shape:** one node object.

**Read-only:** yes. Errors: `invalid_argument`, `node_not_found`.

### 4.3 `related`

| Field | Required | Type | Default | Limits |
| --- | --- | --- | --- | --- |
| `node_id` | yes | string (non-empty) | — | — |
| `limit` | no | positive int | all neighbours | capped at `100` |

Neighbours of a node in either direction (incoming or outgoing edges).
Each entry lists the node and the relationships (`via`) that connect it.

**Success shape:** a list of entries
`{ "node": {...}, "via": [ { "relationship_type": "...", "direction":
"incoming"|"outgoing" } ] }`.

**Ordering guarantee:** deterministic — by neighbour node id ascending;
`via` entries sorted by relationship type then direction.

**Read-only:** yes. Errors: `invalid_argument`, `node_not_found`.

### 4.4 `follow`

| Field | Required | Type | Default | Limits |
| --- | --- | --- | --- | --- |
| `node_id` | yes | string (non-empty) | — | — |
| `relationship_type` | no | canonical relationship kind | all kinds | — |

Traverse existing relationships out of a node. Only relationships whose target
node is loaded are returned. Returns all matching edges.

**Success shape:** a list of edges
`{ "relationship_type": "...", "target_node_id": "...", "label": "...",
"node": {...} }`.

**Ordering guarantee:** deterministic — by relationship type, then target node
id.

**Read-only:** yes. Errors: `invalid_argument`, `invalid_relationship_type`,
`node_not_found`.

### 4.5 `provenance`

| Field | Required | Type | Default | Limits |
| --- | --- | --- | --- | --- |
| `node_id` | yes | string (non-empty) | — | — |

Source/provenance record for a node (never invented; only what exists).

**Success shape:** one provenance record with `node_id`, `source_id`,
`source_name`, `source_version`, `source_location`, `imported_at`, and
evidence fields (`evidence_references`, `evidence_reference_count`).

**Read-only:** yes. Errors: `invalid_argument`, `node_not_found`.

### 4.6 `inspect`

No arguments.

Aggregate database facts: counts by type.

**Success shape:** an object with `source_count`, `node_count`,
`relationship_count`, `nodes_by_type`, `relationships_by_type`.

**Read-only:** yes. Errors: none (rejects unknown/extra arguments with
`invalid_argument`).

---

## 5. Stable error codes

| Code | Meaning | When |
| --- | --- | --- |
| `invalid_request` | request is not a JSON object (or malformed) | non-object request, bad structure |
| `unknown_operation` | requested operation is not defined | unknown `operation` |
| `invalid_argument` | argument missing, wrong type, or out of range | any argument violation |
| `invalid_relationship_type` | `relationship_type` is not a canonical kind | `follow` |
| `node_not_found` | the requested node id does not exist | `get`/`related`/`follow`/`provenance` |
| `internal_error` | unexpected failure behind the boundary | engine fault (no details leak) |

Error responses never contain stack traces or implementation details.

---

## 6. Determinism

* Every list result is ordered by a stable key (documented per operation) —
  no reliance on unordered iteration.
* Repeated identical requests against the same database return identical
  results.

---

## 7. Read-only guarantee

* No public operation writes to the knowledge database. This is enforced by
  the test suite via database-hash checks before/after every operation.

---

## 8. Security invariants

* Unknown operations are rejected (`unknown_operation`).
* Argument keys are strictly whitelisted per operation; requests can never
  smuggle `sql` / `path` / `command` style keys anywhere.
* Argument values are type-checked; limits must be positive integers;
  relationship types must be canonical kinds.
* There is deliberately **no** operation that executes commands, reads
  filesystem paths, or runs arbitrary SQL.

---

## 9. Versioning expectations for consumers

* Pin to the contract version your code was written against (check
  `contract_version` on any response).
* v1 will not silently change. If your needs require a behavior change,
  plan for the next major contract version instead.
* New optional fields may appear in v1 responses; treat unknown fields as
  ignorable.