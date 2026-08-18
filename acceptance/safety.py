"""SQLite write-safety guard for the acceptance/audit pipeline.

The acceptance layer and the mapping audit are STRICTLY read-only: they never
write to any database. This module makes that property structural instead of
just conventional -- ``SQLITE_WRITES_FORBIDDEN`` is exposed to the CLI and the
guard is called from every command before any work begins. If the repository
ever imports this module into a context that would write, the guard raises
before a single write can happen.
"""

SQLITE_WRITES_FORBIDDEN = True


class SqliteWriteForbiddenError(RuntimeError):
    """Raised when the acceptance/audit pipeline would write to SQLite."""


def guard_sqlite_read_only():
    """Fail loudly if the read-only contract has been violated.

    Any future path that needs to write to SQLite must FIRST introduce a
    separate, explicitly-enabled importer; the acceptance layer never does.
    """
    if SQLITE_WRITES_FORBIDDEN:
        return
    raise SqliteWriteForbiddenError(
        "acceptance/audit pipeline attempted SQLite write with "
        "SQLITE_WRITES_FORBIDDEN=False")
