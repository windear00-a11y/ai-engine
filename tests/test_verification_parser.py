"""Verification parser tests — Layer 5 5A-1.

Deterministic, no file writes, no DB mutation, no Contract change.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.indexer import ProjectIndex
from tools.verification.parser import parse


def write_tree(base, files):
    for rel, content in files.items():
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)


class ParserBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "proj")
        os.makedirs(self.root)
        write_tree(self.root, {
            "pkg/__init__.py": "",
            "pkg/a.py": "def foo():\n    return 1\n",
            "tests/test_a.py": "def test_foo():\n    from pkg.a import foo\n    assert foo()==1\n",
        })
        self.idx = ProjectIndex(self.root)
        self.idx.build()
        self.db_path = self.idx.db_path

    def tearDown(self):
        self._tmp.cleanup()

    def parse(self, tool, raw):
        return parse(tool, raw, workspace_root=self.root, db_path=self.db_path)


class TestSyntaxErrorFact(ParserBase):
    def test_syntax_error_fact(self):
        raw = {
            "exit_code": 1,
            "stdout": "",
            "stderr": "pkg/a.py:2: SyntaxError: invalid syntax\n",
            "error": None,
        }
        diags = self.parse("python", raw)
        self.assertEqual(len(diags), 1)
        d = diags[0]
        self.assertEqual(d.kind, "syntax_error")
        self.assertEqual(d.certainty, "fact")
        self.assertEqual(d.file, "pkg/a.py")
        self.assertEqual(d.line, 2)
        self.assertIn("SyntaxError", d.message)


class TestUnittestFact(ParserBase):
    def test_unittest_failure_fact(self):
        raw = {
            "exit_code": 1,
            "stdout": "FAIL: test_foo (tests.test_a.TestA)\n----------------------------------------------------------------------\nTraceback (most recent call last):\n  File \"pkg/a.py\", line 5, in foo\n    assert 1 == 2\nAssertionError: 1 != 2\n",
            "stderr": "",
            "error": None,
        }
        diags = self.parse("python", raw)
        self.assertTrue(any(d.kind == "test_failure" for d in diags))
        d = [x for x in diags if x.kind == "test_failure"][0]
        self.assertEqual(d.certainty, "fact")
        self.assertEqual(d.file, "pkg/a.py")
        self.assertEqual(d.line, 5)
        self.assertIn("AssertionError", d.message)

    def test_unittest_error_fact(self):
        raw = {
            "exit_code": 1,
            "stdout": "ERROR: test_foo (tests.test_a.TestA)\nTraceback (most recent call last):\n  File \"pkg/a.py\", line 8, in bar\n    x = 1/0\nZeroDivisionError: division by zero\n",
            "stderr": "",
            "error": None,
        }
        diags = self.parse("python", raw)
        self.assertTrue(any(d.kind == "test_error" for d in diags))
        d = [x for x in diags if x.kind == "test_error"][0]
        self.assertEqual(d.certainty, "fact")
        self.assertEqual(d.file, "pkg/a.py")

    def test_unittest_with_unknown_file_heuristic(self):
        raw = {
            "exit_code": 1,
            "stdout": "FAIL: test_foo (tests.test_unknown.Test)\nTraceback (most recent call last):\n  File \"unknown/missing.py\", line 10, in foo\n    assert False\nAssertionError: assert False\n",
            "stderr": "",
            "error": None,
        }
        diags = self.parse("python", raw)
        self.assertTrue(len(diags) >= 1)
        d = diags[0]
        # unknown file should be heuristic with file=null
        self.assertEqual(d.certainty, "heuristic")
        self.assertIsNone(d.file)


class TestTracebackFact(ParserBase):
    def test_traceback_fact(self):
        raw = {
            "exit_code": 1,
            "stdout": "Traceback (most recent call last):\n  File \"pkg/a.py\", line 10, in foo\n    x = 1/0\nZeroDivisionError: division by zero\n",
            "stderr": "",
            "error": None,
        }
        diags = self.parse("python", raw)
        self.assertEqual(len(diags), 1)
        d = diags[0]
        self.assertEqual(d.kind, "traceback")
        self.assertEqual(d.certainty, "fact")
        self.assertEqual(d.file, "pkg/a.py")
        self.assertEqual(d.line, 10)
        self.assertIn("ZeroDivisionError", d.message)

    def test_traceback_unknown_file_heuristic(self):
        raw = {
            "exit_code": 1,
            "stdout": "Traceback (most recent call last):\n  File \"unknown.py\", line 5, in foo\n    raise ValueError('x')\nValueError: x\n",
            "stderr": "",
            "error": None,
        }
        diags = self.parse("python", raw)
        self.assertEqual(len(diags), 1)
        d = diags[0]
        self.assertEqual(d.certainty, "heuristic")
        self.assertIsNone(d.file)
        self.assertIsNone(d.line)

    def test_malformed_no_fabrication(self):
        raw = {
            "exit_code": 1,
            "stdout": "some random output without file line\njust noise\n",
            "stderr": "",
            "error": None,
        }
        diags = self.parse("python", raw)
        # Should be heuristic build_error with file=null, not invented
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].kind, "build_error")
        self.assertIsNone(diags[0].file)
        self.assertEqual(diags[0].certainty, "heuristic")

    def test_heuristic_build_error(self):
        raw = {
            "exit_code": 2,
            "stdout": "",
            "stderr": "error: something failed\n",
            "error": None,
        }
        diags = self.parse("python", raw)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0].kind, "build_error")
        self.assertEqual(diags[0].certainty, "heuristic")
        self.assertIsNone(diags[0].file)

    def test_success_returns_empty(self):
        raw = {
            "exit_code": 0,
            "stdout": "OK\nRan 1 test\n",
            "stderr": "",
            "error": None,
        }
        diags = self.parse("python", raw)
        self.assertEqual(diags, [])

    def test_deterministic_ids(self):
        raw = {
            "exit_code": 1,
            "stdout": "pkg/a.py:2: SyntaxError: invalid syntax\n",
            "stderr": "",
            "error": None,
        }
        d1 = self.parse("python", raw)[0]
        d2 = self.parse("python", raw)[0]
        self.assertEqual(d1.id, d2.id)

    def test_duplicate_prevention(self):
        raw = {
            "exit_code": 1,
            "stdout": "Traceback (most recent call last):\n  File \"pkg/a.py\", line 5, in foo\n    x=1/0\nZeroDivisionError: x\nTraceback (most recent call last):\n  File \"pkg/a.py\", line 5, in foo\n    x=1/0\nZeroDivisionError: x\n",
            "stderr": "",
            "error": None,
        }
        diags = self.parse("python", raw)
        # Should deduplicate same file/line/message via id
        ids = [d.id for d in diags]
        self.assertEqual(len(ids), len(set(ids)))
        # Should be 1, not 2 duplicates
        self.assertEqual(len(diags), 1)

    def test_index_grounding(self):
        # File not in index -> heuristic
        raw = {
            "exit_code": 1,
            "stdout": "pkg/missing.py:10: SyntaxError: oops\n",
            "stderr": "",
            "error": None,
        }
        diags = self.parse("python", raw)
        self.assertEqual(diags[0].certainty, "heuristic")
        self.assertIsNone(diags[0].file)

    def test_no_mutation(self):
        import sqlite3
        c = sqlite3.connect(self.db_path)
        before = list(c.execute("SELECT rel_path FROM files ORDER BY rel_path"))
        raw = {
            "exit_code": 1,
            "stdout": "pkg/a.py:2: SyntaxError: invalid\n",
            "stderr": "",
            "error": None,
        }
        self.parse("python", raw)
        after = list(c.execute("SELECT rel_path FROM files ORDER BY rel_path"))
        c.close()
        self.assertEqual(before, after)

    def test_contract_unchanged(self):
        import pathlib
        txt = pathlib.Path("api/contract.py").read_text()
        self.assertIn('CONTRACT_VERSION = "1"', txt)


if __name__ == "__main__":
    unittest.main()
