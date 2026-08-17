"""Deterministic migration/import path from JSON knowledge files to SQLite.

This is the bridge that takes the existing structured JSON knowledge (in
``knowledge/``) and imports it into a :class:`KnowledgeRepository` (SQLite).
It validates each file, imports valid nodes atomically, and records (without
crashing) any invalid files so nothing partial is persisted.
"""

import json
import os

from .knowledge import KnowledgeStore
from .repository import KnowledgeRepository, DEFAULT_KNOWLEDGE_DB


def migrate_json_to_sqlite(json_dir, db_path=DEFAULT_KNOWLEDGE_DB, clear=False):
    """Import every valid JSON knowledge node under ``json_dir`` into SQLite.

    Returns a summary dict with counts and any errors encountered.
    """
    repo = KnowledgeRepository(db_path)
    repo.initialize()
    if clear:
        repo.clear()

    summary = {"sources": 0, "nodes": 0, "relationships": 0, "errors": []}

    for root, _, files in os.walk(json_dir):
        for file in sorted(files):
            if not file.endswith(".json"):
                continue
            path = os.path.join(root, file)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                summary["errors"].append((path, f"invalid JSON: {e}"))
                continue

            ok, msg = KnowledgeStore._validate(data)
            if not ok:
                summary["errors"].append((path, msg))
                continue

            try:
                repo.import_node(os.path.basename(path), path, data)
                summary["sources"] += 1
                summary["nodes"] += 1
                summary["relationships"] += len(data.get("relationships", []))
            except Exception as e:
                summary["errors"].append((path, f"import failed: {e}"))

    return summary


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_KNOWLEDGE_DB
    result = migrate_json_to_sqlite(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "knowledge"), target, clear=True)
    print("Migration summary:", result)
