"""Phase 5: production knowledge.db schema + content is NOT modified.

Phase 5 lifecycle operations run against the caller-provided
KnowledgeRepository. The production ``database/knowledge.db`` must remain
byte-identical (SHA-256 pinned), preserving the master immutable-knowledge
invariant. Lifecycle writes never target knowledge.db directly -- they go into
the metadata JSON column of whichever (test/temp) repository is supplied.
"""

import hashlib
import os
import sqlite3
import unittest

EXPECTED_SHA256 = "000d4fdeb00f09ccb0790330850d3f6f5d34a00b7719c31c8ff7649a503a9a91"

_KNOWLEDGE_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "database", "knowledge.db")


class KnowledgeDbNotModifiedTests(unittest.TestCase):
    def test_production_knowledge_db_sha_unchanged(self):
        with open(_KNOWLEDGE_DB, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        self.assertEqual(digest, EXPECTED_SHA256)

    def test_schema_tables_unchanged(self):
        conn = sqlite3.connect(_KNOWLEDGE_DB)
        try:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertEqual(tables,
                             {"nodes", "sources", "relationships",
                              "sqlite_sequence"})
            nodes_cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(nodes)")]
            self.assertEqual(nodes_cols,
                             ["id", "type", "name", "description",
                              "source_id", "metadata"])
        finally:
            conn.close()

    def test_lifecycle_ops_do_not_touch_production_db(self):
        from intelligence.knowledge.events import LifecycleEventStore
        from intelligence.knowledge.lifecycle import (
            approve_supersede_knowledge,
        )
        from retrieval.repository import KnowledgeRepository

        before = open(_KNOWLEDGE_DB, "rb").read()
        # Exercise lifecycle against an isolated temp repository.
        repo = KnowledgeRepository(":memory:")
        repo.initialize()
        store = LifecycleEventStore(":memory:")  # isolated in-memory evidence
        repo.add_node("old", "fact", "Old", "v1")
        repo.add_node("new", "fact", "New", "v2")
        approve_supersede_knowledge("old", "new", ["ev"], repo, store,
                                    created_at_epoch=1.0)
        repo.close()
        store.close()

        after = open(_KNOWLEDGE_DB, "rb").read()
        self.assertEqual(before, after)
        self.assertEqual(
            hashlib.sha256(after).hexdigest(), EXPECTED_SHA256)


if __name__ == "__main__":
    unittest.main()
