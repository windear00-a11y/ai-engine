# Knowledge Engine CLI

The command-line client (`python -m ai_engine`) is **one client of the
Knowledge Engine Public Contract v1** (see
[docs/public-api-v1.md](public-api-v1.md)). It exercises the same six
operations the contract defines and submits them against the local knowledge
database.

Because the CLI is a client of the contract, this document describes only the
command syntax and presentation choices. It does **not** restate the full
contract specification — consult the contract document for argument types,
defaults, caps, response shapes, error codes, determinism, and read-only
guarantees.

## Operations

| Command | Contract operation | Arguments |
| --- | --- | --- |
| `search` | `search` | `query`, `--type` (`node_type`), `--limit` |
| `get` | `get` | `node_id` |
| `related` | `related` | `node_id`, `--limit` |
| `follow` | `follow` | `node_id`, `--type` (`relationship_type`) |
| `provenance` | `provenance` | `node_id` |
| `inspect` | `inspect` | — |

Every command accepts `--db PATH` to point at a specific knowledge database
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

* Read-only: the CLI never writes to the knowledge database.
* Deterministic: output ordering follows the contract's ordering guarantees.
* No SQL, no path execution, no arbitrary commands: the CLI maps 1:1 onto the
  approved contract operations.