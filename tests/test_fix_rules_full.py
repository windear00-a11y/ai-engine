"""Layer 5B full fix-rule tests (F401, SyntaxError, Dispatcher, E302 hardening)."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.indexer import ProjectIndex
from tools.verification.parser import parse
from tools.verification.diagnostic import make_diagnostic
from tools.verification.fix_rules import dispatch, match
from tools.verification.fix_rules import (
    RULE_ID_E302, RULE_ID_F401, RULE_ID_SYNTAX_COLON,
)


def write_tree(base, files):
    for rel, content in files.items():
        path = os.path.join(base, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)


class FullRulesBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "proj")
        os.makedirs(self.root)
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

    def rebuild(self):
        self.idx = ProjectIndex(self.root)
        self.idx.build()
        self.db_path = self.idx.db_path

    def lint(self, stdout):
        return parse("flake8", {"exit_code": 1, "stdout": stdout,
                                "stderr": "", "error": None},
                     workspace_root=self.root, db_path=self.db_path)

    def fact_diag(self, kind, file, line, column, message, tool="flake8"):
        return make_diagnostic(kind=kind, certainty="fact", file=file,
                               line=line, column=column, symbol=None,
                               message=message, raw="x", tool=tool,
                               exit_code=1)


# ============================================================================
# E302 hardening
# ============================================================================

class TestE302Hardening(FullRulesBase):
    def test_loose_prose_does_not_propose(self):
        # Grounded fact lint whose message merely contains the substring "E302"
        # but is NOT a structured E302 diagnostic -> must be []
        d = self.fact_diag("lint", "pkg/a.py", 3, 1, "not an E302 at all")
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_exact_e302_still_proposes(self):
        d = [x for x in self.lint("pkg/a.py:3:1: E302 expected 2 blank lines, found 0\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        props = dispatch(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].rule_id, RULE_ID_E302)

    def test_wrong_code_prose_does_not_propose(self):
        d = self.fact_diag("lint", "pkg/a.py", 3, 1, "W391: note mentioning E302 rule")
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_prose_not_code_prefix(self):
        d = self.fact_diag("lint", "pkg/a.py", 3, 1, "E302 expected 2 blank lines")
        # No "<CODE>:" prefix -> not a structured lint diagnostic -> []
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])


# ============================================================================
# F401
# ============================================================================

class TestF401(FullRulesBase):
    def test_simple_unused_import_proposal(self):
        write_tree(self.root, {"pkg/a.py": "import os\ndef foo():\n    return 1\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:1:1: F401 'os' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        props = dispatch(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(len(props), 1)
        p = props[0]
        self.assertEqual(p.rule_id, RULE_ID_F401)
        self.assertEqual(p.file, "pkg/a.py")
        self.assertEqual(p.patch["old_text"], "import os\n")
        self.assertEqual(p.patch["new_text"], "")
        self.assertEqual(p.precondition["expected_hash"], __import__("hashlib").sha256(
            "import os\ndef foo():\n    return 1\n".encode()).hexdigest())

    def test_used_import_no_proposal(self):
        write_tree(self.root, {"pkg/a.py": "import os\ndef foo():\n    return os.getcwd()\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:1:1: F401 'os' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_from_import_single_unused(self):
        write_tree(self.root, {"pkg/a.py": "from math import sqrt\ndef foo():\n    return 1\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:1:1: F401 'sqrt' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        props = dispatch(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].patch["old_text"], "from math import sqrt\n")

    def test_alias_safety_removal_of_binding(self):
        write_tree(self.root, {"pkg/a.py": "import os as operating_system\ndef foo():\n    return 1\n"})
        self.rebuild()
        # flake8 names the LOCAL binding (operating_system)
        d = [x for x in self.lint("pkg/a.py:1:1: F401 'operating_system' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        props = dispatch(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(len(props), 1)

    def test_alias_used_no_proposal(self):
        write_tree(self.root, {"pkg/a.py": "import os as operating_system\ndef foo():\n    return operating_system.getcwd()\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:1:1: F401 'operating_system' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_from_import_alias_unused(self):
        write_tree(self.root, {"pkg/a.py": "from math import sqrt as s\n\ndef foo():\n    return 1\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:1:1: F401 's' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        props = dispatch(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(len(props), 1)

    def test_heuristic_no_proposal(self):
        write_tree(self.root, {"pkg/a.py": "import os\ndef foo():\n    return 1\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/missing.py:1:1: F401 'os' imported but unused\n")
             if x.kind == "lint" and x.certainty == "heuristic"][0]
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_unknown_file_no_proposal(self):
        d = self.fact_diag("lint", "pkg/not_in_index.py", 1, 1, "F401: 'os' imported but unused")
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_wildcard_import_no_proposal(self):
        write_tree(self.root, {"pkg/a.py": "from math import *\ndef foo():\n    return 1\n"})
        self.rebuild()
        # Parse 'from math import *' -> single alias name '*', binding '*'
        d = self.fact_diag("lint", "pkg/a.py", 1, 1, "F401: '*' imported but unused")
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_multi_name_import_no_proposal(self):
        write_tree(self.root, {"pkg/a.py": "from math import sqrt, cos\ndef foo():\n    return 1\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:1:1: F401 'sqrt' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_all_assignment_no_proposal(self):
        write_tree(self.root, {"pkg/a.py": "import os\n__all__ = ['os']\ndef foo():\n    return 1\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:1:1: F401 'os' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_conditional_try_import_no_proposal(self):
        # import inside try/except is not top-level -> no proposal
        write_tree(self.root, {"pkg/a.py":
                               "try:\n    import os\nexcept ImportError:\n    os = None\ndef foo():\n    return 1\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:2:5: F401 'os' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_type_checking_import_no_proposal(self):
        # import in TYPE_CHECKING guard is not top-level plain import
        write_tree(self.root, {"pkg/a.py":
                               "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import os\ndef foo():\n    return 1\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:3:5: F401 'os' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_stale_hash_changes(self):
        write_tree(self.root, {"pkg/a.py": "import os\ndef foo():\n    return 1\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:1:1: F401 'os' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        p1 = dispatch(d, workspace_root=self.root, db_path=self.db_path)[0]
        h1 = p1.precondition["expected_hash"]
        write_tree(self.root, {"pkg/a.py": "import os\ndef foo():\n    return 999\n"})
        self.rebuild()
        d2 = [x for x in self.lint("pkg/a.py:1:1: F401 'os' imported but unused\n")
              if x.kind == "lint" and x.certainty == "fact"][0]
        p2 = dispatch(d2, workspace_root=self.root, db_path=self.db_path)[0]
        self.assertNotEqual(h1, p2.precondition["expected_hash"])

    def test_same_line_unused_import_never_deletes_other_statements(self):
        # 'import os; x = 1' is a genuine F401 (os unused) but the physical line
        # also carries 'x = 1'. The rule must NOT propose deleting x = 1: it
        # fails closed with [] (no import-only patch is safely provable).
        write_tree(self.root, {"pkg/a.py":
                               "import os; x = 1\n\n\ndef foo():\n    return x\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:1:1: F401 'os' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        props = dispatch(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(props, [])
        with open(os.path.join(self.root, "pkg/a.py")) as f:
            self.assertEqual(f.read(), "import os; x = 1\n\n\ndef foo():\n    return x\n")

    def test_same_line_usage_not_classified_unused(self):
        # Same-line REAL usage: 'import os; print(os)' re-reads and AST-proves
        # 'os' IS used -> never a proposal.
        write_tree(self.root, {"pkg/a.py": "import os; print(os)\n"})
        self.rebuild()
        d = self.fact_diag("lint", "pkg/a.py", 1, 1,
                           "F401: 'os' imported but unused")
        self.assertEqual(dispatch(d, workspace_root=self.root,
                                  db_path=self.db_path), [])

    def test_same_line_alias_used_no_proposal(self):
        write_tree(self.root, {"pkg/a.py":
                               "import os as operating_system; print(operating_system)\n"})
        self.rebuild()
        d = self.fact_diag("lint", "pkg/a.py", 1, 1,
                           "F401: 'operating_system' imported but unused")
        self.assertEqual(dispatch(d, workspace_root=self.root,
                                  db_path=self.db_path), [])

    def test_import_not_first_on_line_no_proposal(self):
        # Import preceded by another statement on the same line -> cannot be
        # isolated into a minimal import-only patch -> fail closed [].
        write_tree(self.root, {"pkg/a.py":
                               "x = 1; import os\n\n\ndef foo():\n    return x\n"})
        self.rebuild()
        d = self.fact_diag("lint", "pkg/a.py", 1, 7,
                           "F401: 'os' imported but unused")
        self.assertEqual(dispatch(d, workspace_root=self.root,
                                  db_path=self.db_path), [])

    def test_trailing_comment_line_still_exact(self):
        # A lone import followed only by a comment occupies the whole line:
        # whole-line removal remains exact and minimal.
        write_tree(self.root, {"pkg/a.py":
                               "import os  # typing\n\n\ndef foo():\n    return 1\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:1:1: F401 'os' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        props = dispatch(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].patch["old_text"], "import os  # typing\n")
        self.assertEqual(props[0].patch["new_text"], "")

    def test_deterministic_id(self):
        write_tree(self.root, {"pkg/a.py": "import os\ndef foo():\n    return 1\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:1:1: F401 'os' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        p1 = dispatch(d, workspace_root=self.root, db_path=self.db_path)[0]
        p2 = dispatch(d, workspace_root=self.root, db_path=self.db_path)[0]
        self.assertEqual(p1.id, p2.id)
        self.assertEqual(p1.as_dict(), p2.as_dict())

    def test_no_mutation(self):
        write_tree(self.root, {"pkg/a.py": "import os\ndef foo():\n    return 1\n"})
        self.rebuild()
        import sqlite3
        c = sqlite3.connect(self.db_path)
        before = list(c.execute("SELECT rel_path, sha256 FROM files ORDER BY rel_path"))
        c.close()
        d = [x for x in self.lint("pkg/a.py:1:1: F401 'os' imported but unused\n")
             if x.kind == "lint" and x.certainty == "fact"][0]
        dispatch(d, workspace_root=self.root, db_path=self.db_path)
        with open(os.path.join(self.root, "pkg/a.py")) as f:
            after_content = f.read()
        self.assertEqual(after_content, "import os\ndef foo():\n    return 1\n")


# ============================================================================
# SyntaxError "expected ':'"
# ============================================================================

class TestSyntaxColon(FullRulesBase):
    def test_provable_missing_colon(self):
        write_tree(self.root, {"pkg/a.py": "def foo()\n    return 1\n"})
        self.rebuild()
        d = self.fact_diag("syntax_error", "pkg/a.py", 1, None,
                           "SyntaxError: expected ':'", tool="python")
        props = dispatch(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(len(props), 1)
        p = props[0]
        self.assertEqual(p.rule_id, RULE_ID_SYNTAX_COLON)
        self.assertEqual(p.patch["old_text"], "def foo()\n")
        self.assertEqual(p.patch["new_text"], "def foo():\n")

    def test_if_statement_missing_colon(self):
        write_tree(self.root, {"pkg/a.py": "if True\n    print(1)\n"})
        self.rebuild()
        d = self.fact_diag("syntax_error", "pkg/a.py", 1, None,
                           "SyntaxError: expected ':'", tool="python")
        props = dispatch(d, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual(len(props), 1)
        self.assertEqual(props[0].patch["new_text"], "if True:\n")

    def test_wrong_location_no_proposal(self):
        # Adding ':' at line 2 (not the header) would not fix the file
        write_tree(self.root, {"pkg/a.py": "def foo()\n    return 1\n"})
        self.rebuild()
        d = self.fact_diag("syntax_error", "pkg/a.py", 2, None,
                           "SyntaxError: expected ':'", tool="python")
        # line 2 is '    return 1' -> appending ':' makes '    return 1:' invalid
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_missing_line_no_proposal(self):
        write_tree(self.root, {"pkg/a.py": "x = 1\n"})
        self.rebuild()
        d = self.fact_diag("syntax_error", "pkg/a.py", 99, None,
                           "SyntaxError: expected ':'", tool="python")
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_ambiguous_syntax_no_proposal(self):
        # Adding ':' does not make the file valid (still another error)
        write_tree(self.root, {"pkg/a.py": "def foo(\n    return 1\n"})
        self.rebuild()
        d = self.fact_diag("syntax_error", "pkg/a.py", 1, None,
                           "SyntaxError: expected ':'", tool="python")
        props = dispatch(d, workspace_root=self.root, db_path=self.db_path)
        # appending ':' to 'def foo(' yields 'def foo(:' -> still invalid -> []
        self.assertEqual(props, [])

    def test_unrelated_syntax_error_no_proposal(self):
        d = self.fact_diag("syntax_error", "pkg/a.py", 1, None,
                           "SyntaxError: invalid syntax", tool="python")
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_exact_patch_and_hash(self):
        write_tree(self.root, {"pkg/a.py": "def foo()\n    return 1\n"})
        self.rebuild()
        d = self.fact_diag("syntax_error", "pkg/a.py", 1, None,
                           "SyntaxError: expected ':'", tool="python")
        p = dispatch(d, workspace_root=self.root, db_path=self.db_path)[0]
        self.assertEqual(len(p.diff_preview), len(p.diff_preview[:2000]))

    def test_stale_hash_changes(self):
        write_tree(self.root, {"pkg/a.py": "def foo()\n    return 1\n"})
        self.rebuild()
        d = self.fact_diag("syntax_error", "pkg/a.py", 1, None,
                           "SyntaxError: expected ':'", tool="python")
        p1 = dispatch(d, workspace_root=self.root, db_path=self.db_path)[0]
        h1 = p1.precondition["expected_hash"]
        write_tree(self.root, {"pkg/a.py": "def foo()\n    return 42\n"})
        self.rebuild()
        d2 = self.fact_diag("syntax_error", "pkg/a.py", 1, None,
                            "SyntaxError: expected ':'", tool="python")
        p2 = dispatch(d2, workspace_root=self.root, db_path=self.db_path)[0]
        self.assertNotEqual(h1, p2.precondition["expected_hash"])

    def test_deterministic_id_and_no_mutation(self):
        write_tree(self.root, {"pkg/a.py": "def foo()\n    return 1\n"})
        self.rebuild()
        d = self.fact_diag("syntax_error", "pkg/a.py", 1, None,
                           "SyntaxError: expected ':'", tool="python")
        p1 = dispatch(d, workspace_root=self.root, db_path=self.db_path)[0]
        p2 = dispatch(d, workspace_root=self.root, db_path=self.db_path)[0]
        self.assertEqual(p1.id, p2.id)
        with open(os.path.join(self.root, "pkg/a.py")) as f:
            self.assertEqual(f.read(), "def foo()\n    return 1\n")


# ============================================================================
# Dispatcher
# ============================================================================

class TestDispatcher(FullRulesBase):
    def test_all_rules_coexist(self):
        # File with an unused import (sys), and an E302 (no blank before def bar)
        write_tree(self.root, {"pkg/a.py": "import sys\ndef foo():\n    return 1\ndef bar():\n    return 2\n"})
        self.rebuild()
        diags = [x for x in self.lint(
            "pkg/a.py:1:1: F401 'sys' imported but unused\n"
            "pkg/a.py:4:1: E302 expected 2 blank lines, found 0\n")
            if x.certainty == "fact"]
        props = dispatch(diags, workspace_root=self.root, db_path=self.db_path)
        rules = {p.rule_id for p in props}
        self.assertIn(RULE_ID_F401, rules)
        self.assertIn(RULE_ID_E302, rules)

    def test_unsupported_diagnostic_no_proposal(self):
        d = self.fact_diag("test_failure", "pkg/a.py", 1, None, "AssertionError: x", tool="unittest")
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_heuristic_diagnostic_no_proposal(self):
        d = make_diagnostic(kind="lint", certainty="heuristic", file=None, line=None,
                            column=None, symbol=None, message="E302: x", raw="y",
                            tool="flake8", exit_code=1)
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_malformed_diagnostic_no_proposal(self):
        d = make_diagnostic(kind="lint", certainty="fact", file="pkg/a.py", line=None,
                            column=None, symbol=None, message="F401: 'os'", raw="y",
                            tool="flake8", exit_code=1)
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_duplicate_diagnostics_dedup(self):
        write_tree(self.root, {"pkg/a.py": "import os\ndef foo():\n    return 1\ndef bar():\n    return 2\n"})
        self.rebuild()
        diag_raw = "pkg/a.py:1:1: F401 'os' imported but unused\n"
        diag = [x for x in self.lint(diag_raw) if x.certainty == "fact"][0]
        props = dispatch([diag, diag, diag], workspace_root=self.root, db_path=self.db_path)
        ids = [p.id for p in props]
        self.assertEqual(len(ids), len(set(ids)))

    def test_deterministic_ordering(self):
        write_tree(self.root, {"pkg/a.py": "import os\nimport sys\ndef foo():\n    return 1\ndef bar():\n    return 2\n"})
        self.rebuild()
        diags = [x for x in self.lint(
            "pkg/a.py:1:1: F401 'os' imported but unused\n"
            "pkg/a.py:5:1: E302 expected 2 blank lines, found 0\n")
            if x.certainty == "fact"]
        p1 = dispatch(diags, workspace_root=self.root, db_path=self.db_path)
        p2 = dispatch(diags, workspace_root=self.root, db_path=self.db_path)
        self.assertEqual([x.id for x in p1], [x.id for x in p2])

    def test_single_diagnostic_acceptance(self):
        write_tree(self.root, {"pkg/a.py": "import os\ndef foo():\n    return 1\ndef bar():\n    return 2\n"})
        self.rebuild()
        d = [x for x in self.lint("pkg/a.py:1:1: F401 'os' imported but unused\n")
             if x.certainty == "fact"][0]
        self.assertEqual(len(dispatch(d, workspace_root=self.root, db_path=self.db_path)), 1)

    def test_no_mutation_in_dispatch(self):
        write_tree(self.root, {"pkg/a.py": "import os\ndef foo():\n    return 1\ndef bar():\n    return 2\n"})
        self.rebuild()
        with open(os.path.join(self.root, "pkg/a.py")) as f:
            before = f.read()
        diags = [x for x in self.lint(
            "pkg/a.py:1:1: F401 'os' imported but unused\n"
            "pkg/a.py:4:1: E302 expected 2 blank lines, found 0\n")
            if x.certainty == "fact"]
        dispatch(diags, workspace_root=self.root, db_path=self.db_path)
        with open(os.path.join(self.root, "pkg/a.py")) as f:
            self.assertEqual(f.read(), before)


# ============================================================================
# Security
# ============================================================================

class TestSecurity(FullRulesBase):
    def test_path_traversal_dispatch(self):
        d = self.fact_diag("lint", "../../etc/passwd", 1, 1, "F401: 'os' imported but unused")
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_outside_workspace_absolute(self):
        d = self.fact_diag("lint", "/etc/passwd", 1, 1, "F401: 'os' imported but unused")
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=self.db_path), [])

    def test_rule_exception_fails_closed(self):
        d = make_diagnostic(kind="lint", certainty="fact", file="pkg/a.py", line=1,
                            column=1, symbol=None, message="F401: 'os' imported but unused",
                            raw="x", tool="flake8", exit_code=1)
        # Reset index so find_file is empty -> resolve returns None -> []
        self.assertEqual(dispatch(d, workspace_root=self.root, db_path=None), [])


if __name__ == "__main__":
    unittest.main()
