"""Phase 1: context snapshots survive a process restart (file-backed DB).

A snapshot written by one ContextStore (and thus persisted to disk) must be
readable by a fresh ContextStore (and a fresh Python process) pointing at the
same file.
"""

import os
import subprocess
import sys
import tempfile
import unittest

from intelligence.context import ContextStore
from intelligence.context.schema import ContextSnapshot


class ContextStorePersistenceTests(unittest.TestCase):
    def test_snapshot_survives_new_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "context.db")
            store = ContextStore(db_path)
            snap = ContextSnapshot.build(
                system={"os": "linux", "python": "3.11"},
                project={"language": "python", "build": "pip"},
                task={"type": "bug_fix"},
                temporal={}, captured_at_epoch=42.0,
            )
            store.save(snap)
            store.close()

            code = (
                "import sys; sys.path.insert(0, %r);\n"
                "from intelligence.context import ContextStore;\n"
                "s = ContextStore(%r);\n"
                "r = s.get(%r);\n"
                "assert r is not None, 'missing';\n"
                "assert r.project == {'language': 'python', 'build': 'pip'};\n"
                "print(r.context_id)\n"
            ) % (ROOT(), db_path, snap.context_id)
            proc = subprocess.run([sys.executable, "-c", code],
                                  capture_output=True, text=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), snap.context_id)


def ROOT():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


if __name__ == "__main__":
    unittest.main()
