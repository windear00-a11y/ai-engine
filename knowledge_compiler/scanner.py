"""Generic source scanner.

Walks a source directory and produces a deterministic manifest of documents
that a given adapter can parse. Hidden directories (including VCS checkouts
such as ``sources/cpython/.git``) are always skipped, and only files matched by
the adapter's ``supports`` are collected. Ordering is deterministic (sorted).
"""

import os

SKIP_DIRS = {".git", ".hg", ".svn", "__pycache__", ".venv", "venv", ".tox"}


def discover(adapter, source_path):
    """Return (abs_path, rel_path, adapted) tuples for supported files.

    ``rel_path`` is relative to ``source_path`` (or ``source_path`` itself when
    it is a single file). Result is sorted deterministically by rel_path.
    """
    found = []
    source_path = os.path.abspath(source_path)
    if os.path.isfile(source_path):
        if adapter.supports(source_path):
            found.append((source_path, os.path.basename(source_path)))
        return found

    if not os.path.isdir(source_path):
        return found

    base = source_path
    for root, dirs, files in os.walk(source_path):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(files):
            if name.startswith("."):
                continue
            abs_path = os.path.join(root, name)
            if not adapter.supports(abs_path):
                continue
            rel_path = os.path.relpath(abs_path, base)
            found.append((abs_path, rel_path))
    found.sort(key=lambda x: x[1])
    return found


def make_manifest(source_path, found):
    """Deterministic scan report for a set of discovered documents."""
    return {
        "source": os.path.abspath(source_path),
        "document_count": len(found),
        "documents": [rel for _, rel in found],
    }