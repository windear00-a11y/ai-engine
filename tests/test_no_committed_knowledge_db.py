"""Post-purge invariant: no committed knowledge database is shipped.

The development knowledge database at ``database/knowledge.db`` was removed
from the repository and purged from git history. Repository operation must
not require it, and it must not be re-added.

This test asserts the post-purge contract:

- ``database/knowledge.db`` is always git-ignored (even if a tool transiently
  recreates it at the legacy runtime path, it can never be committed);
- ``.gitignore`` blocks ``database/*.db`` so the database cannot be
  accidentally (re-)added;
- no database file is tracked under ``database/``.
"""

import os
import subprocess
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_KNOWLEDGE_DB = os.path.join(_ROOT, "database", "knowledge.db")


class NoCommittedKnowledgeDbTests(unittest.TestCase):
    def test_knowledge_db_git_ignored(self):
        # Even if a runtime tool recreates the legacy-path DB, git must ignore
        # it so it can never be committed back into the repository.
        out = subprocess.run(
            ["git", "check-ignore", "-q", _KNOWLEDGE_DB],
            cwd=_ROOT, capture_output=True)
        self.assertEqual(
            out.returncode, 0,
            "database/knowledge.db must be git-ignored; it was purged from "
            "history")

    def test_gitignore_blocks_database_dbs(self):
        gitignore = os.path.join(_ROOT, ".gitignore")
        with open(gitignore, encoding="utf-8") as fh:
            rules = {line.strip() for line in fh}
        self.assertIn(
            "database/*.db",
            rules,
            ".gitignore must block database/*.db to prevent re-adding the DB")

    def test_no_database_tracked_under_database_dir(self):
        out = subprocess.run(
            ["git", "ls-files", "database/"],
            cwd=_ROOT, capture_output=True, text=True, check=True).stdout
        tracked = [line for line in out.splitlines() if line.endswith(".db")]
        self.assertEqual(tracked, [], "no .db file may be tracked under database/")


if __name__ == "__main__":
    unittest.main()