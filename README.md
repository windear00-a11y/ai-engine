# AI Engine

Local-first persistent intelligence backend. A deterministic, per-project, stdlib-only
system for storing knowledge, recall, lifecycle experience tracking, and provenance
chains. Designed to be consumed as a reusable library by external applications.

**This is not a diary, notes app, or UI.** It is a backend/library that external
projects install and integrate through a documented public boundary.

## Quick Start

```sh
python -m pip install ai_engine-<version>-py3-none-any.whl
```

The wheel contains everything needed (zero runtime dependencies).

## Usage

Three integration patterns; no SQLite, internal schema, or internal-path knowledge required.

### In-process library

```python
from knowledge_client import MemoryClient, HttpMemoryTransport
# or directly:
from ai_engine.lifecycle_service import LifecycleService
```

### CLI

```sh
ai_engine --version     # 0.10.0
ai_engine init
ai_engine remember "service domain facts"
ai_engine recall "domain facts"
ai_engine status
ai_engine doctor        # diagnostics
```

`ai-engine` is accepted as an alias.

### HTTP server

```sh
ai_engine serve --host 127.0.0.1 --port 8765
# POST /v2/execute, GET /health
```

### SDK over HTTP (remote)

```python
from knowledge_client import MemoryClient, HttpMemoryTransport
client = MemoryClient(HttpMemoryTransport(host="127.0.0.1", port=8765))
result = client.remember(payload={"text": "hello"}, project_id="myapp")
results = client.recall(query="hello", project_id="myapp")
```

## How It Works

ai-engine stores data under a configurable **data root** (one directory per project):

- `AI_ENGINE_DATA_DIR` environment variable, **or**
- `$XDG_DATA_HOME/ai-engine`, **or**
- `~/.ai-engine` (default)

Project data is isolated by project ID. Packages installed from the wheel are immutable;
uninstalling does not delete data. See [docs/INTEGRATION.md](docs/INTEGRATION.md) for the
complete integration reference.

## Build

```sh
python -m pip install --upgrade setuptools wheel build
python -m build
```

Wheel and sdist are written to `dist/`.

## Requirements

- Python >= 3.10 (stdlib only at runtime)
- A Python with pip/venv to build (build tooling is not a runtime dependency)

## Vocabulary

`diary_v1` (the generic vocabulary) ships as package data inside the wheel. The retired
`code_v1` vocabulary is no longer shipped.

## Architecture Overview

Four surfaces for external consumers:

| Surface | Package / entry point | Primary use |
|---|---|---|
| SDK (remote) | `knowledge_client.MemoryClient` + `HttpMemoryTransport` | HTTP client from any language env |
| In-process | `ai_engine.lifecycle_service.LifecycleService` | Python application with direct DB access |
| CLI | `ai_engine` console command | Shell integration, scripting |
| HTTP server | `ai_engine serve` | REST backend for web/mobile clients |

Public contract: `api/contract_v2.py` (17 operations, frozen).
Legacy contract: `api/contract.py` (6 read-only operations, frozen).

All internal storage (`intelligence/*`, `retrieval/*`, `tools/permissions/*`) is an
implementation detail and must not be imported directly by external consumers.
See [docs/INTEGRATION.md](docs/INTEGRATION.md) for the public/internal boundary.

## Known Limitations

- The v1 legacy HTTP `/v1/execute` endpoint requires passing `--db` in a standalone
  install (the default path is a repo-relative fallback). Use `/v2/execute` for new
  integrations.
- The system Python on some platforms may lack pip/ensurepip; use a Python that provides
  pip/venv to build and install.