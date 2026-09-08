# KGHEER Core — External Integration Reference

This document is the authoritative guide for consuming **KGHEER Core** (the
open-source `kgheer-core` Python distribution) from an external project. It defines the
**supported public boundary**, the integration patterns, the HTTP v2 envelope,
project/data isolation, and the modules external consumers must **not** depend on.

**KGHEER Core** is the open-source technical foundation of the KGHEER project
ecosystem (KGHEER SDK, KGHEER CLI, KGHEER Diary, KGHEER Notes, KGHEER Plugins).
This document keeps the historical module/import names (`ai_engine`,
`knowledge_client`, ...) because they are the stable technical contract.

---

## 1. What KGHEER Core is

KGHEER Core is a **local-first, deterministic, per-project persistent intelligence
backend** (stdlib-only, no runtime dependencies). It is a library/backend, **not** a
diary, notes, or UI application. External applications install the wheel and integrate
through the public surfaces below.

---

## 2. Installation

### 2.1 Install from PyPI (recommended)

```sh
python -m pip install kgheer-core
```

### 2.2 Build a wheel from source (contributors)

```sh
python -m pip install --upgrade setuptools wheel build
python -m build
```

Artifacts (wheel + sdist) are written to `dist/`.

### 2.3 Install the built wheel

```sh
python -m pip install dist/kgheer_core-<version>-py3-none-any.whl
```

Install into any clean environment. The wheel is self-contained; **do not** put the
source checkout on `PYTHONPATH`. Runtime dependencies: **none** (Python stdlib only;
requires Python >= 3.10). (PyPI distribution `kgheer-core`; Python imports
`ai_engine` / `knowledge_client`; CLI `ai-engine` / `ai_engine`; data directory
`~/.ai-engine`.)

---

## 3. The public integration boundary

There are **four** officially supported surfaces. Everything not listed here is an
internal implementation detail.

| Surface | Entry point | Transport | Notes |
|---|---|---|---|
| SDK (remote) | `knowledge_client.MemoryClient` | `HttpMemoryTransport` (stdlib `http.client`) | Recommended for remote/any-language clients |
| SDK (in-process / stdio) | `knowledge_client.MemoryClient` + `MemoryInProcessTransport` / `SessionTransport` | in-process / subprocess | Requires the `kgheer-core` distribution installed in the consuming interpreter |
| In-process library | `ai_engine.lifecycle_service.LifecycleService` | direct | Direct access to the lifecycle/trace API |
| CLI | `ai_engine` / `ai-engine` console command | process | Shell scripting |
| HTTP server | `ai_engine serve` | HTTP | `/health`, `/v2/execute`, `/v1/execute` (legacy) |

### Official public packages

- `knowledge_client` — the SDK. Its top-level module has **no imports** from internal
  packages, and the remote (`HttpMemoryTransport`/`HttpTransport`) transports are fully
  independent of internals. The in-process/stdio transports (`MemoryInProcessTransport`,
  `InProcessTransport`, `SessionTransport`, `MemorySessionTransport`) intentionally
  lazy-import internal handlers because they execute within the consuming interpreter —
  they require the `kgheer-core` distribution installed in that interpreter.
- `api.contract_v2` / `api.contract` — immutable contract specifications (vanilla type
  definitions and validators — importable for validation but not required).

### Public classes/functions

- `knowledge_client.MemoryClient` — v2 SDK (`remember`, `recall`, `get`, `provenance`,
  `inspect`, `context_get`, `plan` → `lifecycle.plan`), plus generic `request(operation,
  arguments)` / `execute(request)` passthroughs for any v2 operation.
- `knowledge_client.transports.{HttpMemoryTransport, HttpTransport,
  MemoryInProcessTransport, MemorySessionTransport, SessionTransport, InProcessTransport,
  OneShotTransport}`.
- `ai_engine.lifecycle_service.LifecycleService` — in-process lifecycle API.
- `ai_engine.memory.Memory` — in-process memory/vocabulary API.
- `ai_engine.__main__` — the CLI entrypoint (via the `ai_engine` console script).

### What consumers must NOT depend on

The following are **internal implementation details** and are subject to change without
notice. Do not import them from an external project:

- `intelligence/*` — internal stores, schema, lifecycle models, trace internals.
- `retrieval/*` — internal repositories and knowledge graph storage.
- `tools/permissions/*` — internal permission/trust layer.
- `ingestion/*`, `importing/*`, `external_import/*` — internal ingestion pipelines.
- `http_server/*` — internal HTTP server implementation (use the CLI `serve` instead).
- `external_http_client/*` — legacy internal client (use `knowledge_client` instead).
- `api/memory_api.py`, `api/memory_tools.py`, `api/tools.py` — internal API/handlers
  (the contracts in `api/contract*.py` are the stable spec).

Consumers never need to know what database files or schema are used.

### Repository-level public/internal boundary

The following repository paths are **internal operational development material**,
not part of the public API or integration surface. They are not shipped in the wheel
and may change or disappear without notice:

- `benchmarks/` — internal performance harnesses.
- `.workflow/`, `.opencode/` — internal development automation/state.
- `output/`, `tmp/`, `workspace/` — generated or scratch artifacts.
- `database/` — internal persisted state used by development/verification. The
  committed `database/knowledge.db` is a canonical backup for tests; it is not a
  supported integration surface and its schema is internal.

Public-facing integration docs are `README.md`, `docs/INTEGRATION.md`,
`docs/OPEN_SOURCE_POLICY.md`, `docs/cli.md`, and the contract specs under `api/`.

---

## 4. Data root and project/data isolation

Data lives under a single configurable **data root**, resolved in this priority order:

1. `$AI_ENGINE_DATA_DIR` (if set and non-empty)
2. `$XDG_DATA_HOME/ai-engine`
3. `~/.ai-engine`

Each project is stored in its own directory under the data root:
`<data_root>/<project_id>/`. Project IDs are validated as
`^[a-z0-9_-]{1,64}$` (lowercase letters, digits, `-`, `_`; 1–64 chars).

**Isolation guarantee**: a recall/query against one project never returns records
from another project. Isolate two consumers by using distinct project IDs (or distinct
data roots). No public operation requires knowledge of the underlying files.

---

## 5. HTTP v2 integration (`/v2/execute`)

### Envelope

`POST /v2/execute` with `Content-Type: application/json`:

```json
{
  "operation": "<v2 operation name>",
  "arguments": { "<argument name>": "<value>", "...": "..." }
}
```

**Important:** arguments are nested under `arguments`. Do **not** put operation
arguments at the top level next to `operation`.

Response envelope (HTTP 200 on success):

```json
{
  "ok": true,
  "operation": "<operation>",
  "contract_version": "2",
  "result": { "...": "..." }
}
```

Requests that fail validation return structured `ok: false` with an
`error.code`/`error.message`. Any operation may accept a `project_id` argument;
if omitted, the default project (`default`) is used. `project_id` must match
`^[a-z0-9_-]{1,64}$`.

### `payload` and `remember`

The v2 `remember` operation takes structured memory content under the `payload`
argument (a JSON object), e.g.:

```json
{
  "operation": "remember",
  "arguments": {
    "project_id": "myapp",
    "payload": { "text": "the service was deployed", "metadata": {"env": "prod"} }
  }
}
```

`recall` takes `query`, plus optional `limit`, `candidate_limit`, `context`,
`project_id`, `vocabulary_id`:

```json
{
  "operation": "recall",
  "arguments": { "project_id": "myapp", "query": "deployed", "limit": 10 }
}
```

### Health

`GET /health` returns contract versions + live metrics (200).

### Authentication (optional)

If the server is started with `--api-key`, every `POST` requires `X-API-Key` or
`Authorization: Bearer <key>`.

---

## 6. Python / SDK integration

### HTTP (remote, recommended)

```python
from knowledge_client import MemoryClient, HttpMemoryTransport

client = MemoryClient(HttpMemoryTransport(host="127.0.0.1", port=8765))
client.remember(payload={"text": "hello from app"}, project_id="myapp")
res = client.recall(query="hello", project_id="myapp")
```

### In-process (same interpreter, no server)

The in-process transports execute against the installed package directly and require
the `kgheer-core` distribution to be installed in the consuming interpreter:

```python
from knowledge_client import MemoryClient, MemoryInProcessTransport

client = MemoryClient(MemoryInProcessTransport(data_root="/tmp/my_data"))
client.remember(payload={"text": "hello"}, project_id="app_a")
```

---

## 7. In-process library integration

Use the lifecycle service directly when embedding ai-engine in a Python application:

```python
from ai_engine.lifecycle_service import LifecycleService

svc = LifecycleService(project_id="app_a", data_root="/tmp/my_data")
ev = svc.record_evidence("observation", "a claim")
xp = svc.record_experience("a situation", "an attempt", "success",
                           evidence_ids=[ev["evidence_id"]])
info = svc.describe(xp["experience_id"])

# Derive learning / strategies / plans, trace provenance:
trace = svc.trace(xp["experience_id"])
```

`LifecycleService(project_id=..., data_root=...)` keeps all data under
`<data_root>/<project_id>/` and is fully deterministic.

---

## 8. CLI integration

```sh
# Initialize a project
ai_engine init --project myapp

# Ingest / remember
ai_engine remember --project myapp "a memorable fact"
ai_engine lifecycle ingest --project myapp "other knowledge"

# Record an experience
ai_engine lifecycle experience --project myapp "situation X" "attempt Y" success

# Recall
ai_engine recall --project myapp "domain fact"

# Status / diagnostics
ai_engine status --project myapp
ai_engine doctor
```

`AI_ENGINE_DATA_DIR` controls the data root when set. `ai-engine` aliases `ai_engine`.

---

## 8.5 Verification examples (real, verified interfaces)

These examples use only interfaces verified to exist in the wheel:

1. **HTTP roundtrip** — see section 5 code block (verified: `remember` → `recall` →
   `lifecycle.ingest` all return `ok: true`).
2. **In-process** — see section above (verified: `LifecycleService` `record_experience`
   → `describe` returns a derived `context_id`).
3. **CLI** — see section above (verified in clean install).

---

## 9. Versioning & compatibility

- `CONTRACT_VERSION = "1"` (v1, read-only, frozen).
- `CONTRACT_VERSION = "2"` (v2, 17 operations, additive, frozen).

Both are backward compatible; v2 is the current surface. No public operation is renamed
or removed.

---

## 10. Known limitations

- Legacy v1 HTTP `/v1/execute` requires an explicit `--db` in a standalone install
  (the default is a repo-relative fallback). Prefer `/v2/execute` for new integrations.
- On some platforms the default system Python lacks pip/ensurepip; use a Python that
  provides pip/venv to build and install.