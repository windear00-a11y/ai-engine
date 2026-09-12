# database/

The `database/` directory is reserved for the legacy runtime knowledge-database
path convention `<repo>/database/<db>` used by v1 tooling compatibility
defaults.

## Status: no committed database

A development knowledge database at `database/knowledge.db` was previously
committed to this repository as a content-bearing artifact. It has been
**removed** and **purged from git history**, and must not be re-added:

- it is **not committed** and is **not present** in this directory;
- it is **not** shipped in the `kgheer-core` wheel or sdist;
- repository operation and tests do **not** require it; tests use isolated
  temporary or in-memory databases.

## Legacy runtime compatibility

For backward compatibility, legacy v1 tooling still resolves the default
knowledge database through `ai_engine.paths.get_legacy_db_path`, which points
to `<repo>/database/<db_name>` (for example `database/knowledge.db`) when an
explicit path is not supplied. This is a runtime path convention only — no file
in `database/` is required for installation, tests, or normal operation.

## Relationship to runtime user data

Runtime user data is separate from this directory. A fresh `kgheer-core`
installation stores user knowledge under per-project databases created lazily
on first use in the resolved data root:

- `$AI_ENGINE_DATA_DIR`
- → `$XDG_DATA_HOME/ai-engine`
- → `~/.ai-engine`

Each project owns its own databases (`knowledge.db`, `context.db`,
`evidence.db`, …) under `<data_root>/<project_id>/`; these are entirely
separate from the legacy `<repo>/database` convention and never constrain it.

## Policy

- **Do not re-add** any database under `database/`. `.gitignore` blocks
  `database/*.db` for this reason.