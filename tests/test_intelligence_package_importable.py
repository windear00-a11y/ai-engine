"""Phase 0: the empty ``intelligence`` package must import cleanly.

The package is deliberately empty at Phase 0. Importing it must succeed on a
plain stdlib Python interpreter with no third-party or AI/ML dependencies and
must not import or modify any production component.
"""

import os
import subprocess
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..")) \
    if os.path.dirname(__file__) else os.getcwd()

# Recompute robustly: tests/ lives inside the project root.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


class IntelligencePackageImportableTests(unittest.TestCase):
    def test_import_intelligence_in_process(self):
        import intelligence  # noqa: F401
        self.assertTrue(intelligence.__version__ == "0")

    def test_import_via_subprocess_stdlib_only(self):
        # Confirm the package imports in a fresh interpreter with only stdlib
        # on the path (no site packages, no ML framework).
        code = "import sys; sys.path.insert(0, %r); import intelligence; " \
               "print(intelligence.__version__)" % _ROOT
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0,
                         msg=proc.stderr)
        self.assertEqual(proc.stdout.strip(), "0")

    def test_package_exposes_expected_subpackages(self):
        import intelligence
        package_dir = os.path.dirname(intelligence.__file__)
        entries = sorted(
            e for e in os.listdir(package_dir)
            if not e.endswith((".pyc", ".pyo")) and e != "__pycache__"
        )
        # Phase 10 adds the api subpackage; Phase 26 adds lifecycle.
        self.assertEqual(entries, ["__init__.py", "api", "context", "decision",
                                   "evidence", "experience", "knowledge",
                                   "learning", "lifecycle", "loop", "outcome",
                                   "policies", "reasoning", "strategy"])


if __name__ == "__main__":
    unittest.main()
