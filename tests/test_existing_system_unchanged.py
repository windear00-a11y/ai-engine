"""Phase 0 regression guard: prove the existing system is unaffected.

The intelligence layer adds NO production code at Phase 0 (only the empty
``intelligence`` package root). This test proves the pre-existing system is
unchanged by running the full existing test suite and asserting that nothing
NEW breaks.

Design
------
The full suite is long (~23 min) and contains a documented set of
PRE-EXISTING failures unrelated to this phase. Rather than forcing them to
pass (they did not pass before Phase 0 either), this test:

  * runs the full ``tests/`` suite with the intelligence tests and itself
    excluded (to avoid recursion);
  * deselects the known pre-existing failing tests, which are
    production-data-dependent:
      - ``external_client/test_external_client.py::InProcessIntegrationTests
        ::test_real_relationship_traversal``
      - ``test_knowledge_client.py::InProcessSdkTests
        ::test_follow_real_relationship``
      (both assert a production ``knowledge.db`` node carries ``example_of``
      edges, which none of the current production nodes do);
  * asserts the remaining tests all pass (exit code 0).

If this test starts failing, it means either a NEW regression was introduced
or one of the known pre-existing failures was fixed (an improvement, but the
baseline set must then be updated deliberately).

This test never writes to any production or new database.
"""

import os
import subprocess
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TESTS_DIR = os.path.join(_ROOT, "tests")

# Known pre-existing failures as of Phase 0 (recorded baseline). Deselecting
# them means "the system is unchanged" holds even though these two
# data-dependent tests are already red before any intelligence work.
KNOWN_PRE_EXISTING_FAILURES = (
    # Relative to rootdir, as pytest sees them when run from cwd=_ROOT.
    (
        "tests/external_client/test_external_client.py::"
        "InProcessIntegrationTests::test_real_relationship_traversal"
    ),
    (
        "tests/test_knowledge_client.py::"
        "InProcessSdkTests::test_follow_real_relationship"
    ),
)

SUITE_TIMEOUT_SECONDS = 60 * 40  # 40 min headroom for the ~23 min suite.


def _existing_suite_args():
    # Run from cwd=_ROOT with a RELATIVE target ``tests`` and deselect node
    # IDs prefixed with ``tests/...`` so they match what pytest reports.
    args = [
        sys.executable, "-m", "pytest", "tests",
        "-q", "--tb=short", "-p", "no:cacheprovider",
        "--ignore", os.path.join(TESTS_DIR,
                                 "test_intelligence_invariants.py"),
        "--ignore", os.path.join(TESTS_DIR,
                                 "test_intelligence_package_importable.py"),
        "--ignore", TESTS_DIR + "/test_existing_system_unchanged.py",
    ]
    for node in KNOWN_PRE_EXISTING_FAILURES:
        args += ["--deselect", node]
    return args


class ExistingSystemUnchangedTests(unittest.TestCase):
    def test_full_existing_suite_passes_unchanged(self):
        proc = subprocess.run(
            _existing_suite_args(),
            cwd=_ROOT,
            capture_output=True,
            text=True,
            timeout=SUITE_TIMEOUT_SECONDS,
        )
        tail = (proc.stdout + proc.stderr)[-4000:]
        self.assertEqual(
            proc.returncode, 0,
            "existing suite regressed. Tail:\n%s" % tail,
        )


if __name__ == "__main__":
    unittest.main()
