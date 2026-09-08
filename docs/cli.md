# Knowledge Engine CLI

The command-line client (`ai-engine`, equivalently `python -m ai_engine`) is part of
**KGHEER Core**'s public surface: a front end over the public contract. Its primary
surface is the **v2 / lifecycle CLI** (see [docs/public-contract.md](public-contract.md)):
memory operations that persist in per-project stores and a nine-command `lifecycle`
subcommand that drives the canonical backend loop (acquisition, experience, learning,
strategy, planning, and authority-gated execution). The six v1 operations remain
available as legacy subcommands that are read-only clients of Public Contract v1 (see
[docs/public-api-v1.md](public-api-v1.md)).

This document describes command syntax and presentation choices. It does
**not** restate the full contract specification — consult the contract
documents for argument types, defaults, caps, response shapes, error codes,
determinism, and read-only guarantees.

## Primary CLI (v2 / lifecycle)

All commands accept `--project PROJECT` (default `default`) and operate on the
project's isolated per-project databases under the data root.

| Command | Purpose |
| --- | --- |
| `init` | initialize the data root and default project |
| `remember` | store a structured memory payload (v2 `remember`) |
| `recall` | recall memories and experiences (v2 `recall`) |
| `capture manual` | capture a source record through the capture adapter |
| `context show` / `context diff` | show a project context snapshot / diff two contexts |
| `lifecycle ingest` (`lc1`) | ingest a user fact or external knowledge |
| `lifecycle experience` (`lc2`) | record an experience |
| `lifecycle learning` (`lc3`) | derive a learning record from experiences |
| `lifecycle strategy` (`lc4`) | derive strategies from experiences |
| `lifecycle trace` (`lc5`) | trace a record's provenance |
| `lifecycle describe` (`lc6`) | describe any lifecycle record |
| `lifecycle summary` (`lc7`) | summarize project lifecycle state |
| `lifecycle plan` (`lc8`) | draft a plan from experiences (no execution) |
| `lifecycle execute` (`lc9`) | execute a planned step under authority approval |
| `status` / `backup` / `export` / `import` / `doctor` / `serve` / `migrate` / `restore` | project administration |

## Legacy v1 operations

| Command | Contract operation | Arguments |
| --- | --- | --- |
| `search` | `search` | `query`, `--type` (`node_type`), `--limit` |
| `get` | `get` | `node_id` |
| `related` | `related` | `node_id`, `--limit` |
| `follow` | `follow` | `node_id`, `--type` (`relationship_type`) |
| `provenance` | `provenance` | `node_id` |
| `inspect` | `inspect` | — |

Every v1 command accepts `--db PATH` to point at a specific knowledge database
(default: `database/knowledge.db`).

## Presentation

The CLI is a *presentation* client: it may reshape results for a terminal but
never changes their meaning.

* **`search`** is compact by default: each result is reduced to identity
  fields (`id`, `type`, `name`, `summary`, `score`) so large node payloads are
  not dumped to the terminal. `--verbose` shows fuller payloads under a hard
  byte budget.
* Other commands print JSON with stable key ordering.
* Errors print JSON on stdout plus a human-readable line on stderr, and exit
  non-zero.

## Behavior guarantees

* Read-only guarantee applies to the **v1 operations** only (they never write
  to the knowledge database). The v2/lifecycle commands persist records in
  per-project stores by design; `lifecycle execute` runs a step's effect only
  after authority approval.
* Deterministic: output ordering follows the contract's ordering guarantees.
* No SQL, no path execution, no arbitrary commands: the CLI maps 1:1 onto the
  approved contract operations.