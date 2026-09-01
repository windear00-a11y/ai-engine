"""Fix rules tests — Layer 5 5B-1a (E302 only)."""

import os
import sys
import tempfile
import unittest
import hashlib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.indexer import ProjectIndex
from tools.verification.parser import parse
from tools.verification.fix_rules import match


def write_tree(base, files):
    for rel, content in files.items():
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)


class FixRulesBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "proj")
        os.makedirs(self.root)
        # Base file with E302 condition: def bar immediately after def foo without blank lines
        write_tree(self.root, {
            "pkg/__init__.py": "",
            "pkg/a.py": "def foo():\n    return 1\ndef bar():\n    return 2\n",
            "tests/test_a.py": "def test_foo():\n    assert 1==1\n",
        })
        self.idx = ProjectIndex(self.root)
        self.idx.build()
        self.db_path = self.idx.db_path

    def tearDown(self):
        self._tmp.cleanup()

    def parse_lint(self, stdout):
        raw = {"exit_code": 1, "stdout": stdout, "stderr": "", "error": None}
        diags = parse("flake8", raw, workspace_root=self.root, db_path=self.db_path)
        return diags

    def get_e302_diag(self, stdout="pkg/a.py:3:1: E302 expected 2 blank lines, found 0\n"):
        diags = self.parse_lint(stdout)
        # Find E302 lint fact
        for d in diags:
            if d.kind == "lint" and "E302" in d.message and d.certainty == "fact":
                return d
        return None


class TestE302GroundedFact(FixRulesBase):
    def test_e302_grounded_fact_proposal(self):
        d = self.get_e302_diag()
        self.assertIsNotNone(d)
        self.assertEqual(d.certainty, "fact")
        proposals = match(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertEqual(p.rule_id, "e302_blank_lines")
        self.assertEqual(p.file, "pkg/a.py")
        self.assertEqual(p.diagnostic_id, d.id)
        self.assertEqual(p.certainty, "fact")
        self.assertEqual(p.risk, "low")
        self.assertIn("E302", p.description)
        # Patch should be old_text/new_text suitable for file.edit
        self.assertIn("def bar", p.patch["old_text"])
        self.assertEqual(p.patch["new_text"], "\n" + p.patch["old_text"])
        self.assertIn("diff_preview", p.as_dict())
        self.assertTrue(p.diff_preview.startswith("---"))

    def test_heuristic_e302_no_proposal(self):
        raw = {"exit_code": 1, "stdout": "pkg/missing.py:12:5: E302 expected 2 blank lines\n", "stderr": "", "error": None}
        diags = parse("flake8", raw, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(diags[0].certainty, "heuristic")
        proposals = match(diags[0], workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(proposals, [])

    def test_ungrounded_file_no_proposal(self):
        # Create diagnostic with heuristic file null directly
        from tools.verification.diagnostic import make_diagnostic
        d = make_diagnostic(kind="lint", certainty="heuristic", file=None, line=None, column=None, symbol=None, message="E302: expected 2 blank lines", raw="pkg/missing.py:12:5: E302", tool="flake8", exit_code=1)
        proposals = match(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(proposals, [])

    def test_already_correct_blank_line_no_proposal(self):
        # File already has 2 blank lines before def bar
        write_tree(self.root, {"pkg/a.py": "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"})
        self.idx.build()
        # Re-parse same diagnostic but now source has blank lines
        raw = {"exit_code": 1, "stdout": "pkg/a.py:5:1: E302 expected 2 blank lines, found 0\n", "stderr": "", "error": None}
        diags = parse("flake8", raw, workspace_root=self.root, db_path=self.db_path)
        d = [x for x in diags if x.kind == "lint" and "E302" in x.message][0]
        proposals = match(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(proposals, [])

    def test_ambiguous_source_no_proposal(self):
        # Make file with duplicate line content "def bar():\n" appearing twice
        write_tree(self.root, {"pkg/a.py": "def foo():\n    return 1\ndef bar():\n    return 2\ndef bar():\n    return 3\n"})
        self.idx.build()
        raw = {"exit_code": 1, "stdout": "pkg/a.py:3:1: E302 expected 2 blank lines\n", "stderr": "", "error": None}
        diags = parse("flake8", raw, workspace_root=self.root, db_path=self.db_path)
        d = [x for x in diags if "E302" in x.message][0]
        proposals = match(d, workspace_root=self.root, db_path=self.db_path)
        # old_text appears twice, so ambiguous -> []
        self.assertEqual(proposals, [])

    def test_stale_hash_no_unsafe(self):
        d = self.get_e302_diag()
        proposals = match(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        # Modify file after proposal
        write_tree(self.root, {"pkg/a.py": "def foo():\n    return 99\ndef bar():\n    return 2\n"})
        # Re-match same diagnostic (same id) but source changed: should still produce proposal with new hash, but old proposal's expected_hash is now stale
        # The test verifies that new proposal has different expected_hash and id
        proposals2 = match(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(len(proposals2), 1)
        p2 = proposals2[0]
        self.assertNotEqual(p.precondition["expected_hash"], p2.precondition["expected_hash"])
        self.assertNotEqual(p.id, p2.id)

    def test_deterministic_id(self):
        d = self.get_e302_diag()
        p1 = match(d, workspace_root=self.root, db_path=self.db_path)[0]
        p2 = match(d, workspace_root=self.root, db_path=self.db_path)[0]
        self.assertEqual(p1.id, p2.id)
        self.assertEqual(p1.as_dict()["diff_preview"], p2.as_dict()["diff_preview"])

    def test_expected_hash_changes(self):
        d = self.get_e302_diag()
        p1 = match(d, workspace_root=self.root, db_path=self.db_path)[0]
        h1 = p1.precondition["expected_hash"]
        write_tree(self.root, {"pkg/a.py": "def foo():\n    return 999\ndef bar():\n    return 2\n"})
        self.idx.build()
        p2 = match(d, workspace_root=self.root, db_path=self.db_path)[0]
        h2 = p2.precondition["expected_hash"]
        self.assertNotEqual(h1, h2)

    def test_path_traversal_no_proposal(self):
        from tools.verification.diagnostic import make_diagnostic
        d = make_diagnostic(kind="lint", certainty="fact", file="../../etc/passwd", line=1, column=1, symbol=None, message="E302: expected", raw="x", tool="flake8", exit_code=1)
        proposals = match(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(proposals, [])

    def test_no_mutation(self):
        import sqlite3
        c = sqlite3.connect(self.db_path)
        before = list(c.execute("SELECT rel_path, sha256 FROM files ORDER BY rel_path"))
        d = self.get_e302_diag()
        match(d, workspace_root=self.root, db_path=self.db_path)
        after = list(c.execute("SELECT rel_path, sha256 FROM files ORDER BY rel_path"))
        c.close()
        self.assertEqual(before, after)
        # Also check file not modified
        with open(os.path.join(self.root, "pkg/a.py"), "r") as f:
            content_before = f.read()
        match(d, workspace_root=self.root, db_path=self.db_path)
        with open(os.path.join(self.root, "pkg/a.py"), "r") as f:
            content_after = f.read()
        self.assertEqual(content_before, content_after)

    def test_no_subprocess(self):
        import pathlib
        txt = pathlib.Path("tools/verification/fix_rules.py").read_text()
        self.assertNotIn("subprocess", txt)
        self.assertNotIn("eval(", txt)
        self.assertNotIn("exec(", txt)
        self.assertNotIn("os.system", txt)

    def test_build_error_no_proposal(self):
        raw = {"exit_code": 1, "stdout": "error: something", "stderr": "", "error": None}
        diags = parse("python", raw, workspace_root=self.root, db_path=self.db_path)
        # Should be build_error heuristic
        self.assertEqual(diags[0].kind, "build_error")
        proposals = match(diags[0], workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(proposals, [])

    def test_f401_no_proposal(self):
        raw = {"exit_code": 1, "stdout": "pkg/a.py:1:1: F401 'os' imported but unused\n", "stderr": "", "error": None}
        diags = parse("flake8", raw, workspace_root=self.root, db_path=self.db_path)
        d = [x for x in diags if "F401" in x.message][0]
        proposals = match(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(proposals, [])

    def test_syntax_error_no_proposal(self):
        from tools.verification.diagnostic import make_diagnostic
        d = make_diagnostic(kind="syntax_error", certainty="fact", file="pkg/a.py", line=2, column=None, symbol=None, message="SyntaxError: expected ':'", raw="x", tool="python", exit_code=1)
        proposals = match(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(proposals, [])


if __name__ == "__main__":
    unittest.main()
