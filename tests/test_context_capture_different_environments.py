"""Phase 1: different environments -> different context_ids.

Changing any similarity dimension (system, project, task) must change the
deterministic context_id.
"""

import os
import tempfile
import unittest

from intelligence.context import capture_context
from intelligence.context.schema import derive_context_id


class ContextCaptureDifferentEnvironmentsTests(unittest.TestCase):
    def test_different_task_metadata_differs(self):
        a = capture_context(task_metadata={"type": "bug_fix"})
        b = capture_context(task_metadata={"type": "refactor"})
        self.assertNotEqual(a.context_id, b.context_id)
        self.assertNotEqual(a.task, b.task)

    def test_different_project_root_differs(self):
        with tempfile.TemporaryDirectory() as py_root:
            with tempfile.TemporaryDirectory() as js_root:
                # Make the two roots genuinely different projects.
                with open(os.path.join(py_root, "pyproject.toml"), "w") as f:
                    f.write("[project]\nname='x'\n")
                with open(os.path.join(js_root, "package.json"), "w") as f:
                    f.write("{}\n")
                a = capture_context(project_root=py_root)
                b = capture_context(project_root=js_root)
                self.assertNotEqual(a.project, b.project)
                self.assertNotEqual(a.context_id, b.context_id)

    def test_os_change_changes_id(self):
        sys_a = {"os": "linux", "os_version": "A", "arch": "x86_64",
                 "python": "3.11"}
        sys_b = {"os": "windows", "os_version": "A", "arch": "x86_64",
                 "python": "3.11"}
        id_a = derive_context_id(sys_a, {}, {})
        id_b = derive_context_id(sys_b, {}, {})
        self.assertNotEqual(id_a, id_b)
        self.assertTrue(a_id_starts_ctx(id_a))
        self.assertTrue(a_id_starts_ctx(id_b))


def a_id_starts_ctx(cid):
    return cid.startswith("ctx_")


if __name__ == "__main__":
    unittest.main()
