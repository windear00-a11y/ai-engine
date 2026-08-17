import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.coding import (
    CodingTools, Workspace, PathError, file_list, project_inspect,
)


def write(root, rel, content, binary=False):
    path = os.path.join(root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if binary:
        with open(path, "wb") as f:
            f.write(content)
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)


class WorkspaceSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ws = Workspace(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_resolve_inside_root_ok(self):
        self.assertTrue(self.ws.resolve("a/b.txt").startswith(self.ws.root))

    def test_resolve_dotdot_rejected(self):
        with self.assertRaises(PathError):
            self.ws.resolve("../escape")

    def test_resolve_absolute_outside_rejected(self):
        with self.assertRaises(PathError):
            self.ws.resolve("/etc/passwd")

    def test_resolve_absolute_inside_allowed(self):
        inside = os.path.join(self.ws.root, "x.txt")
        self.assertEqual(self.ws.resolve(inside), os.path.realpath(inside))

    def test_relative_traversal_into_sibling_rejected(self):
        with self.assertRaises(PathError):
            self.ws.resolve("a/../../outside")

    def test_missing_root_raises(self):
        with self.assertRaises(PathError):
            Workspace(os.path.join(self.tmp.name, "does-not-exist"))


class FileListTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ct = CodingTools(self.tmp.name)
        write(self.tmp.name, "a/b/c/file.txt", "hello")
        write(self.tmp.name, "a/b/file2.txt", "hi")
        write(self.tmp.name, "top.txt", "x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_lists_immediate_children(self):
        res = self.ct.file_list(".", depth=0)
        self.assertIsNone(res["error"])
        paths = {e["path"] for e in res["entries"]}
        self.assertIn("top.txt", paths)
        self.assertIn("a", paths)
        self.assertNotIn(os.path.join("a", "b"), paths)

    def test_depth_descends(self):
        res = self.ct.file_list(".", depth=2)
        paths = {e["path"] for e in res["entries"]}
        self.assertIn(os.path.join("a", "b", "c"), paths)

    def test_traversal_attempt_returns_error(self):
        res = self.ct.file_list("../escape", depth=1)
        self.assertIsNotNone(res["error"])

    def test_missing_path_error(self):
        res = self.ct.file_list("nope", depth=1)
        self.assertIn("error", res)
        self.assertEqual(res["entries"], [])

    def test_file_as_path_error(self):
        res = self.ct.file_list("top.txt", depth=1)
        self.assertIn("error", res)


class FileReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ct = CodingTools(self.tmp.name)
        write(self.tmp.name, "src/app.py", "print('hi')\nx=1\n")
        write(self.tmp.name, "data.bin", b"\x00\x01\x02binary", binary=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_content_and_size(self):
        res = self.ct.file_read("src/app.py")
        self.assertIsNone(res["error"])
        self.assertIn("print", res["content"])
        self.assertEqual(res["size"], len("print('hi')\nx=1\n"))

    def test_missing_file_error(self):
        res = self.ct.file_read("missing.py")
        self.assertIn("error", res)
        self.assertIsNone(res["content"])

    def test_directory_error(self):
        res = self.ct.file_read("src")
        self.assertIn("error", res)

    def test_binary_error(self):
        res = self.ct.file_read("data.bin")
        self.assertIn("error", res)
        self.assertIsNone(res["content"])

    def test_traversal_rejected(self):
        res = self.ct.file_read("../../etc/passwd")
        self.assertIn("error", res)
        self.assertIsNone(res["content"])

    def test_max_size_enforced(self):
        res = self.ct.file_read("src/app.py", max_size=2)
        self.assertIn("error", res)


class FileSearchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ct = CodingTools(self.tmp.name)
        write(self.tmp.name, "src/app.py", "def main():\n    return 42\n")
        write(self.tmp.name, "src/util.js", "function helper() {}\n")
        write(self.tmp.name, "notes.txt", "main is the entrypoint\n")
        write(self.tmp.name, "blob.bin", b"\x00main\x00", binary=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_finds_matches_across_files(self):
        res = self.ct.file_search("main")
        self.assertIsNone(res["error"])
        found = {f["path"] for f in res["files"]}
        self.assertIn("src/app.py", found)
        self.assertIn("notes.txt", found)

    def test_skips_binary(self):
        res = self.ct.file_search("main")
        paths = {f["path"] for f in res["files"]}
        self.assertNotIn("blob.bin", paths)

    def test_case_insensitive_by_default(self):
        res = self.ct.file_search("MAIN")
        paths = {f["path"] for f in res["files"]}
        self.assertIn("src/app.py", paths)

    def test_case_sensitive_option(self):
        res = self.ct.file_search("MAIN", case_sensitive=True)
        self.assertEqual(res["files"], [])

    def test_include_ext_filter(self):
        res = self.ct.file_search("main", include_ext=[".py"])
        paths = {f["path"] for f in res["files"]}
        self.assertEqual(paths, {"src/app.py"})

    def test_result_limits(self):
        write(self.tmp.name, "many.txt", "\n".join(f"main {i}" for i in range(50)))
        res = self.ct.file_search("main", max_results=10)
        self.assertLessEqual(res["total_matches"], 10)

    def test_traversal_rejected(self):
        res = self.ct.file_search("main", path="../escape")
        self.assertIn("error", res)


class ProjectInspectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        write(self.tmp.name, "package.json", "{}")
        write(self.tmp.name, "pyproject.toml", "[tool.poetry]")
        write(self.tmp.name, "README.md", "# Project")
        write(self.tmp.name, "src/app.py", "print(1)\n")
        write(self.tmp.name, "src/main.py", "print(2)\n")
        write(self.tmp.name, "lib/util.js", "console.log(1)\n")
        write(self.tmp.name, "tests/test_app.py", "def test_x(): pass\n")
        self.ct = CodingTools(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_detects_languages(self):
        res = self.ct.project_inspect()
        self.assertIsNone(res["error"])
        self.assertIn("python", res["languages"])
        self.assertIn("javascript", res["languages"])

    def test_detects_project_types(self):
        res = self.ct.project_inspect()
        self.assertIn("python", res["project_types"])
        self.assertIn("node", res["project_types"])

    def test_detects_config_files(self):
        res = self.ct.project_inspect()
        names = {c["name"] for c in res["config_files"]}
        self.assertIn("package.json", names)
        self.assertIn("pyproject.toml", names)
        self.assertIn("README.md", names)

    def test_detects_test_dirs(self):
        res = self.ct.project_inspect()
        self.assertIn("tests", res["test_dirs"])

    def test_reports_root(self):
        res = self.ct.project_inspect()
        self.assertEqual(res["root"], os.path.realpath(self.tmp.name))

    def test_empty_workspace_no_error(self):
        empty = tempfile.TemporaryDirectory()
        try:
            res = CodingTools(empty.name).project_inspect()
            self.assertIsNone(res["error"])
            self.assertEqual(res["file_count"], 0)
        finally:
            empty.cleanup()


class CodeAnalyzeTests(unittest.TestCase):
    PY = (
        "import os\n"
        "from sys import version\n"
        "from . import sibling\n"
        "\n"
        "__all__ = ['PublicThing']\n"
        "\n"
        "def helper(x):\n"
        "    return x\n"
        "\n"
        "async def fetcher():\n"
        "    pass\n"
        "\n"
        "class PublicThing:\n"
        "    def method(self):\n"
        "        pass\n"
    )
    JS = (
        "import React from 'react';\n"
        "const util = require('lodash');\n"
        "function helper() {}\n"
        "class Widget {}\n"
        "export default helper;\n"
        "export const api = 1;\n"
    )

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ct = CodingTools(self.tmp.name)
        write(self.tmp.name, "mod.py", self.PY)
        write(self.tmp.name, "widget.js", self.JS)
        write(self.tmp.name, "readme.txt", "not code")
        write(self.tmp.name, "img.bin", b"\x00img", binary=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_python_extracts_structure(self):
        res = self.ct.code_analyze("mod.py")
        self.assertIsNone(res["error"])
        self.assertEqual(res["language"], "python")
        self.assertIn("os", res["imports"])
        self.assertIn("sys", res["imports"])
        names = {f["name"] for f in res["functions"]}
        self.assertIn("helper", names)
        self.assertIn("fetcher", names)
        cls = {c["name"] for c in res["classes"]}
        self.assertIn("PublicThing", cls)
        self.assertIn("PublicThing", res["exports"])

    def test_jsts_extracts_structure(self):
        res = self.ct.code_analyze("widget.js")
        self.assertIsNone(res["error"])
        self.assertEqual(res["language"], "javascript")
        self.assertIn("react", res["imports"])
        self.assertIn("lodash", res["imports"])
        names = {f["name"] for f in res["functions"]}
        self.assertIn("helper", names)
        cls = {c["name"] for c in res["classes"]}
        self.assertIn("Widget", cls)
        self.assertIn("default", res["exports"])
        self.assertIn("api", res["exports"])

    def test_unsupported_extension_error(self):
        res = self.ct.code_analyze("readme.txt")
        self.assertIn("error", res)
        self.assertIsNone(res["language"])

    def test_binary_error(self):
        res = self.ct.code_analyze("img.bin")
        self.assertIn("error", res)

    def test_traversal_rejected(self):
        res = self.ct.code_analyze("../../etc/passwd")
        self.assertIn("error", res)

    def test_missing_file_error(self):
        res = self.ct.code_analyze("nope.py")
        self.assertIn("error", res)


class DefaultWorkspaceTests(unittest.TestCase):
    def test_module_functions_run_without_error(self):
        # Default workspace ("workspace/") exists but is empty.
        res = project_inspect()
        self.assertIn("root", res)
        res2 = file_list(".")
        self.assertIn("entries", res2)


if __name__ == "__main__":
    unittest.main()
