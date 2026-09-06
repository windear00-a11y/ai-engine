"""Slice 4C tests — pip list/freeze/check exact forms.

Covers:
- pip list/freeze/check allowed
- pip install/show etc denied
- extra args denied
- shell/chaining denied
- approval required
- no args="any" for pip
- python -c regression
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


class Slice4CPipAllowedTests(unittest.TestCase):
    def test_pip_list_allowed(self):
        pol = _policy()
        self.assertIsNotNone(check_args(pol, "pip", ["list"]))

    def test_pip_freeze_allowed(self):
        pol = _policy()
        self.assertIsNotNone(check_args(pol, "pip", ["freeze"]))

    def test_pip_check_allowed(self):
        pol = _policy()
        self.assertIsNotNone(check_args(pol, "pip", ["check"]))


class Slice4CPipDeniedTests(unittest.TestCase):
    def test_pip_install_denied(self):
        pol = _policy()
        for args in [["install", "requests"], ["install", "-r", "requirements.txt"], ["install", "-e", "."], ["install"]]:
            with self.assertRaises(CommandDenied):
                check_args(pol, "pip", args)

    def test_pip_show_denied(self):
        pol = _policy()
        with self.assertRaises(CommandDenied):
            check_args(pol, "pip", ["show", "requests"])

    def test_pip_uninstall_download_wheel_denied(self):
        pol = _policy()
        for args in [["uninstall", "requests"], ["download", "requests"], ["wheel", "."]]:
            with self.assertRaises(CommandDenied):
                check_args(pol, "pip", args)

    def test_pip_config_denied(self):
        pol = _policy()
        with self.assertRaises(CommandDenied):
            check_args(pol, "pip", ["config", "list"])


class Slice4CExtraArgsDeniedTests(unittest.TestCase):
    def test_extra_args_denied(self):
        pol = _policy()
        with self.assertRaises(CommandDenied):
            check_args(pol, "pip", ["list", "--extra"])
        with self.assertRaises(CommandDenied):
            check_args(pol, "pip", ["freeze", "--extra"])
        with self.assertRaises(CommandDenied):
            check_args(pol, "pip", ["check", "--extra"])
        with self.assertRaises(CommandDenied):
            check_args(pol, "pip", ["list", "."])
        with self.assertRaises(CommandDenied):
            check_args(pol, "pip", [])


class Slice4CShellChainingTests(unittest.TestCase):
    def test_shell_chaining_denied(self):
        pol = _policy()
        for args in [
            ["list", ";", "ls"],
            ["freeze", "&"],
            ["check", "|", "cat"],
            ["list", ">", "out"],
        ]:
            with self.assertRaises(CommandDenied):
                check_args(pol, "pip", args)


class Slice4CApprovalTests(unittest.TestCase):
    def test_approval_required(self):
        pol = _policy()
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        for args in [["list"], ["freeze"], ["check"]]:
            r = run_checked(pol, "pip", args, lambda p: False, cwd=root,
                            workspace_root=root)
            self.assertIn("not approved", (r["error"] or "").lower())
            self.assertFalse(r["success"])
            r2 = run_checked(pol, "pip", args, lambda p: True, cwd=root,
                             workspace_root=root)
            self.assertNotIn("denied", (r2["error"] or "").lower())
            self.assertNotIn("not approved", (r2["error"] or "").lower())

    def test_no_args_any_for_pip(self):
        pol = _policy()
        spec = pol.command_spec("pip") or {}
        forms = spec.get("forms") or []
        self.assertTrue(forms)
        for f in forms:
            self.assertNotEqual(f.get("args"), "any")
            self.assertIsNotNone(f.get("args"))
        # also ensure check_args still enforces exact forms (not any)
        with self.assertRaises(CommandDenied):
            check_args(pol, "pip", ["list", "freeze"])


class Slice4CPythonRegressionTests(unittest.TestCase):
    def test_python_c_still_denied(self):
        pol = _policy()
        for name in ("python", "python3"):
            with self.assertRaises(CommandDenied):
                check_args(pol, name, ["-c", "print(1)"])
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        r = run_checked(pol, "python", ["-c", "print(1)"], lambda p: True, cwd=root)
        self.assertIn("denied", r["error"])
        self.assertFalse(r["success"])


if __name__ == "__main__":
    unittest.main()
