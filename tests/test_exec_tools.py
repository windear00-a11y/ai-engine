import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.coding import (
    CodingTools, ExecutionRunner, ProjectVerificationTools,
)

# A restrictive allowlist for exercising rejection paths.
ALLOWLIST = {
    "python": {"executable": sys.executable, "args": "any"},
    "pyver": {"executable": sys.executable, "args": ["--version"]},
}


def write(root, rel, content):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


class ExecutionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runner = ExecutionRunner(self.tmp.name, allowlist=ALLOWLIST)

    def tearDown(self):
        self.tmp.cleanup()

    def test_successful_execution(self):
        res = self.runner.run("python", ["-c", "print('hello')"])
        self.assertIsNone(res["error"])
        self.assertEqual(res["exit_code"], 0)
        self.assertIn("hello", res["stdout"])
        self.assertTrue(res["success"])

    def test_rejected_executable(self):
        res = self.runner.run("ls", ["-la"])
        self.assertIn("error", res)
        self.assertIn("not allowed", res["error"])
        self.assertFalse(res["success"])

    def test_invalid_argument(self):
        # pyver only allows "--version"
        res = self.runner.run("pyver", ["-c", "x"])
        self.assertIn("error", res)
        self.assertIn("argument not allowed", res["error"])

    def test_valid_argument(self):
        res = self.runner.run("pyver", ["--version"])
        self.assertIsNone(res["error"])
        self.assertEqual(res["exit_code"], 0)

    def test_stdout_stderr_capture(self):
        code = "import sys; print('out'); print('err', file=sys.stderr)"
        res = self.runner.run("python", ["-c", code])
        self.assertIn("out", res["stdout"])
        self.assertIn("err", res["stderr"])

    def test_nonzero_exit_code(self):
        res = self.runner.run("python", ["-c", "import sys; sys.exit(3)"])
        self.assertEqual(res["exit_code"], 3)
        self.assertFalse(res["success"])
        self.assertIsNone(res["error"])

    def test_timeout(self):
        code = "import time; time.sleep(30)"
        res = self.runner.run("python", ["-c", code], timeout=1)
        self.assertTrue(res["timed_out"])
        self.assertFalse(res["success"])
        self.assertIn("terminated", res["error"])

    def test_cwd_inside_workspace(self):
        write(self.tmp.name, "sub/file.txt", "x")
        res = self.runner.run("python", ["-c", "print('ok')"], cwd="sub")
        self.assertIsNone(res["error"])
        self.assertEqual(res["cwd"], "sub")

    def test_cwd_traversal_rejected(self):
        res = self.runner.run("python", ["-c", "print(1)"], cwd="../escape")
        self.assertIn("error", res)
        self.assertFalse(res["success"])

    def test_cwd_absolute_escape_rejected(self):
        res = self.runner.run("python", ["-c", "print(1)"], cwd="/etc")
        self.assertIn("error", res)

    def test_no_workspace_pyc_pollution(self):
        write(self.tmp.name, "mod.py", "x = 1\n")
        self.runner.run("python", ["-c", "import mod"], cwd=".")
        # ensure no __pycache__ written into the workspace
        for current, dirs, _ in os.walk(self.tmp.name):
            self.assertNotIn("__pycache__", dirs)


class ProjectTestBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.runner = ExecutionRunner(self.tmp.name, allowlist=ALLOWLIST)
        self.ct = CodingTools(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_python_project(self):
        write(self.tmp.name, "app.py", "def add(a, b):\n    return a + b\n")
        write(self.tmp.name, "tests/__init__.py", "")
        write(self.tmp.name, "tests/test_app.py",
              "import unittest\n"
              "from app import add\n"
              "\n"
              "class TestApp(unittest.TestCase):\n"
              "    def test_add(self):\n"
              "        self.assertEqual(add(1, 2), 3)\n")

    def test_project_test_runs_unittest(self):
        self._make_python_project()
        res = self.ct.project_test()
        self.assertIsNone(res["error"])
        self.assertEqual(res["exit_code"], 0)
        self.assertTrue(res["success"])
        self.assertIn("python -m unittest", res["detected"])

    def test_project_test_no_config(self):
        res = self.ct.project_test()
        self.assertIn("error", res)
        self.assertFalse(res["success"])

    def test_project_build_compiles(self):
        self._make_python_project()
        res = self.ct.project_build()
        self.assertIsNone(res["error"])
        self.assertEqual(res["exit_code"], 0)
        self.assertTrue(res["success"])

    def test_project_build_no_config(self):
        res = self.ct.project_build()
        self.assertIn("error", res)

    def test_project_check_passes(self):
        self._make_python_project()
        res = self.ct.project_check()
        self.assertIsNone(res["error"])
        self.assertTrue(res["success"])
        names = {c["name"] for c in res["checks"]}
        self.assertIn("python_syntax", names)

    def test_project_check_syntax_error(self):
        write(self.tmp.name, "bad.py", "def broken(:\n    pass\n")
        res = self.ct.project_check()
        self.assertFalse(res["success"])
        bad = [c for c in res["checks"] if c["name"] == "python_syntax"][0]
        self.assertEqual(bad["status"], "fail")

    def test_project_check_js_config_missing_ref(self):
        write(self.tmp.name, "package.json",
              '{"main": "does_not_exist.js"}')
        res = self.ct.project_check()
        js = [c for c in res["checks"] if c["name"] == "js_config_references"]
        self.assertTrue(js)
        self.assertEqual(js[0]["status"], "fail")


class ModuleLevelTests(unittest.TestCase):
    def test_module_functions_smoke(self):
        # default workspace is empty; verification should report no config.
        from tools.coding import project_test, project_build, project_check
        self.assertIn("error", project_test())
        self.assertIn("error", project_build())
        self.assertIn("checks", project_check())


if __name__ == "__main__":
    unittest.main()
