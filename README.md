# AI Engine — Persistent Intelligence System

Local-first persistent intelligence: a generic diary/memory/experience system for local recall and decisions. Stdlib-only runtime (no runtime dependencies), deterministic-first.

## Requirements

- Python >= 3.10 (stdlib only at runtime)
- A Python interpreter with pip/venv to build and install (build tooling is not a runtime dependency)

## Build

```sh
python -m pip install --upgrade setuptools wheel build
python -m build
```

Artifacts are written to `dist/`: a wheel (`ai_engine-<version>-py3-none-any.whl`) and a source distribution.

## Install

```sh
python -m pip install dist/ai_engine-<version>-py3-none-any.whl
```

Install the wheel into any clean Python environment. The checkout itself must not be on `PYTHONPATH`.

## Run

```sh
ai_engine --version     # print version
ai_engine init          # initialize a project (default data root)
ai_engine remember      # store a remembered item
ai_engine recall        # recall stored items
ai_engine status        # project state
ai_engine doctor        # environment/data-root diagnostics
```

`ai-engine` is accepted as an alias for `ai_engine`.

## Runtime data

Runtime data (project databases) lives under the configured data root, never inside the installed package directory:

- `AI_ENGINE_DATA_DIR` environment variable, or
- the default data root under the user config/data directory.

Installed package files are immutable. Uninstalling/reinstalling the package does not delete user data. Multiple projects are isolated under the data root.

## Vocabularies

`diary_v1` (generic) and `code_v1` (optional Code domain) ship as package data inside the installed `ai_engine` package and resolve without reference to a source checkout.

## Optional Code plugin

Coding/tooling is an opt-in domain. Generic `import ai_engine` never imports it. Code plugin availability after installation depends on its modules being importable (see `ai_engine.plugins.code`).

## Known platform limitations

- The repository's default system Python may lack pip/setuptools/ensurepip; use a Python interpreter that provides pip/venv to build and to create the clean install environment.