# database/

This directory holds the project's committed knowledge database:

- `database/knowledge.db` is the **canonical backup** of the KGHEER Core
  knowledge graph used by development and verification. It is intentionally
  committed as content-bearing data; a SHA-256 integrity pin asserted by
  `tests/test_knowledge_db_not_modified.py` guarantees it is never silently
  changed.
- It is a **repository-level artefact only**: the released `kgheer-core`
  wheel and sdist do not contain `database/` or any `.db` file, so a fresh
  installation never receives this database — or the CPython documentation
  content it contains — as initial or default knowledge.
- It is **not** a supported integration surface. External consumers interact
  with the engine only through the public contract, SDK, CLI, or HTTP server
  (see `docs/INTEGRATION.md`).

## Relationship to runtime user data

This file is deliberately **separate** from user runtime data. A fresh
`kgheer-core` installation stores user knowledge under per-project databases
created lazily on first use in the resolved data root:

- `$AI_ENGINE_DATA_DIR`
- → `$XDG_DATA_HOME/ai-engine`
- → `~/.ai-engine`

Each project owns its own databases (`knowledge.db`, `context.db`,
`evidence.db`, …), which are entirely separate from this committed file. The
SHA-256 pin above is a **repository/test integrity mechanism** for the
committed artefact only; it is not a user-runtime storage requirement and
never constrains per-project user data.

## Known secret-scan false positives

Almost all of the database's knowledge content was ingested from public
CPython documentation (docs.python.org) library pages in a single earlier
import during development. Because of that, a small number of nodes match
secret-looking heuristics while containing **no real secrets**:

- Nodes quoting the CPython `secrets` module recipe that builds a generated
  password string — a documentation example of correct API usage, not a
  credential.
- Nodes quoting the CPython `configparser` documentation example whose sample
  section is named "topsecret" — fictional example data, not a secret.
- One node reproduces the CPython SSL documentation's **PEM placeholder** for
  TLS private keys. Its text is placeholder prose (a format sketch of a
  private-key header block) with **no key material**; it is an illustrative
  string from `docs.python.org`, not a real private key. No credentials or key
  material are present anywhere in the database.

## Policy

- **Do not rewrite or scrub the database** merely to silence these audit
  false positives: the database is content-bearing and its integrity hash is
  pinned. Deleting or rewriting nodes would corrupt the canonical content and
  break the pin.
- **This exemption does not generalize.** Only the verified documentation
  placeholder described above is known-safe. Any other occurrence of a PEM
  private-key header block — now or in future data — must be treated as a
  real security concern until verified.
- If a secret scanner is used in CI, these known strings may be **allowlisted
  with narrowly scoped, path-specific rules** (limited to
  `database/knowledge.db` and to the exact verified node content), not
  globally suppressed.
