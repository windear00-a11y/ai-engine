"""Slice 4A tests — read-only linters/formatters exact forms.

Covers:
- allowed exact forms for flake8, ruff, black, isort
- wrong args denied
- shell/chaining denied
- python -c still denied
- approval still required
- existing execution tests remain passing (via regression)
- legacy python args="any" investigation (no silent change)
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.permissions import Policy
from tools.permissions.execution import check_args, run_checked, CommandDenied


def _policy():
    return Policy()


class Slice4AAllowedFormsTests(unittest.TestCase):
    def test_flake8_allowed_exact_forms(self):
        pol = _policy()
        # flake8 . and flake8 --count . are allowed
        self.assertIsNotNone(check_args(pol, "flake8", ["."]))
        self.assertIsNotNone(check_args(pol, "flake8", ["--count", "."]))

    def test_ruff_allowed_exact_form(self):
        pol = _policy()
        self.assertIsNotNone(check_args(pol, "ruff", ["check", "."]))

    def test_black_allowed_exact_form(self):
        pol = _policy()
        self.assertIsNotNone(check_args(pol, "black", ["--check", "."]))

    def test_isort_allowed_exact_form(self):
        pol = _policy()
        self.assertIsNotNone(check_args(pol, "isort", ["--check-only", "."]))


class Slice4AWrongArgsDeniedTests(unittest.TestCase):
    def test_flake8_wrong_args_denied(self):
        pol = _policy()
        with self.assertRaises(CommandDenied):
            check_args(pol, "flake8", ["--invalid"])
        with self.assertRaises(CommandDenied):
            check_args(pol, "flake8", ["--count", "--count"])
        with self.assertRaises(CommandDenied):
            check_args(pol, "flake8", [])

    def test_ruff_wrong_args_denied(self):
        pol = _policy()
        with self.assertRaises(CommandDenied):
            check_args(pol, "ruff", ["check"])
        with self.assertRaises(CommandDenied):
            check_args(pol, "ruff", ["."])
        with self.assertRaises(CommandDenied):
            check_args(pol, "ruff", ["check", ".", "--extra"])

    def test_black_wrong_args_denied(self):
        pol = _policy()
        with self.assertRaises(CommandDenied):
            check_args(pol, "black", ["--check"])
        with self.assertRaises(CommandDenied):
            check_args(pol, "black", ["."])
        with self.assertRaises(CommandDenied):
            check_args(pol, "black", ["--check", ".", "--extra"])

    def test_isort_wrong_args_denied(self):
        pol = _policy()
        with self.assertRaises(CommandDenied):
            check_args(pol, "isort", ["--check-only"])
        with self.assertRaises(CommandDenied):
            check_args(pol, "isort", ["."])


class Slice4AShellChainingDeniedTests(unittest.TestCase):
    def test_shell_chars_denied(self):
        pol = _policy()
        for cmd, args in [
            ("flake8", [";", "ls"]),
            ("ruff", ["check", ".", "&"]),
            ("black", ["--check", ".", "|", "cat"]),
            ("isort", ["--check-only", ".", ">", "out"]),
        ]:
            with self.assertRaises(CommandDenied, msg=f"{cmd} {args}"):
                check_args(pol, cmd, args)

    def test_python_c_still_denied(self):
        pol = _policy()
        for name in ("python", "python3"):
            with self.assertRaises(CommandDenied):
                check_args(pol, name, ["-c", "print(1)"])
            with self.assertRaises(CommandDenied):
                check_args(pol, name, ["-c"])
        # also via run_checked
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        r = run_checked(pol, "python", ["-c", "print(1)"], lambda p: True, cwd=root)
        self.assertIn("denied", r["error"])
        self.assertFalse(r["success"])


class Slice4AApprovalRequiredTests(unittest.TestCase):
    def test_approval_still_required_for_new_commands(self):
        pol = _policy()
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        for cmd, args in [
            ("flake8", ["."]),
            ("ruff", ["check", "."]),
            ("black", ["--check", "."]),
            ("isort", ["--check-only", "."]),
        ]:
            r = run_checked(pol, cmd, args, lambda p: False, cwd=root,
                            workspace_root=root)
            self.assertIn("not approved", r["error"].lower() if r["error"] else "")
            self.assertFalse(r["success"])
            # with approve=True, should not be denied (may fail to execute if
            # binary missing, but not denied)
            r2 = run_checked(pol, cmd, args, lambda p: True, cwd=root,
                             workspace_root=root)
            # Should not be "denied" or "not approved" — may be "failed to
            # execute" if binary absent, that's ok
            self.assertNotIn("denied", (r2["error"] or "").lower())
            self.assertNotIn("not approved", (r2["error"] or "").lower())


class Slice4AClosedFormsInvestigation(unittest.TestCase):
    """The legacy coding facade used a Python args="any" allowlist escape hatch.

    Phase 24 removes that facade; the Policy command specs are now the single
    source of truth. Every allowed command uses CLOSED arg forms (no
    wildcard "any"), so the legacy gap is closed rather than silently kept.
    """

    def test_no_wildcard_any_forms_remain(self):
        pol = _policy()
        for name in ("python", "python3", "flake8", "ruff", "black",
                     "isort", "npm"):
            spec = pol.command_spec(name) or {}
            forms = spec.get("forms") or []
            self.assertTrue(forms, name)
            for f in forms:
                self.assertIsNotNone(f.get("args"), name)
                self.assertNotEqual(f.get("args"), "any", name)
        # git / make have no closed form at all -> denied (deny-by-default)
        self.assertIsNone(pol.command_spec("git"))
        self.assertIsNone(pol.command_spec("make"))


if __name__ == "__main__":
    unittest.main()
