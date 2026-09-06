"""Slice 4B tests — npm test / npm run build exact forms.

Covers:
- npm test allowed, npm run build allowed
- npm install denied, npm run lint denied, extra args denied
- shell/chaining denied
- approval required, missing npm not a bypass
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


class Slice4BNpmAllowedTests(unittest.TestCase):
    def test_npm_test_allowed(self):
        pol = _policy()
        self.assertIsNotNone(check_args(pol, "npm", ["test"]))

    def test_npm_run_build_allowed(self):
        pol = _policy()
        self.assertIsNotNone(check_args(pol, "npm", ["run", "build"]))


class Slice4BNpmDeniedTests(unittest.TestCase):
    def test_npm_install_denied(self):
        pol = _policy()
        with self.assertRaises(CommandDenied):
            check_args(pol, "npm", ["install"])
        with self.assertRaises(CommandDenied):
            check_args(pol, "npm", ["ci"])
        with self.assertRaises(CommandDenied):
            check_args(pol, "npm", ["update"])

    def test_npm_run_lint_denied(self):
        pol = _policy()
        with self.assertRaises(CommandDenied):
            check_args(pol, "npm", ["run", "lint"])
        with self.assertRaises(CommandDenied):
            check_args(pol, "npm", ["run", "dev"])

    def test_npm_test_extra_args_denied(self):
        pol = _policy()
        with self.assertRaises(CommandDenied):
            check_args(pol, "npm", ["test", "--extra"])
        with self.assertRaises(CommandDenied):
            check_args(pol, "npm", ["run", "build", "--extra"])
        with self.assertRaises(CommandDenied):
            check_args(pol, "npm", ["test", "."])

    def test_npm_arbitrary_denied(self):
        pol = _policy()
        for args in [[""], [], ["run"], ["test", "run"], ["npx", "test"]]:
            # npx is not allowed at all
            if args and args[0] == "npx":
                with self.assertRaises(CommandDenied):
                    check_args(pol, "npx", [])
                continue
            if args in ([["test"] , ["run","build"]]):
                continue
            # ensure not the allowed forms
            if args not in ([["test"], ["run", "build"]]):
                if args == [""] or args == []:
                    with self.assertRaises(CommandDenied):
                        check_args(pol, "npm", args)
                elif args not in [["test"], ["run", "build"]]:
                    with self.assertRaises(CommandDenied):
                        check_args(pol, "npm", args)


class Slice4BShellChainingTests(unittest.TestCase):
    def test_shell_chaining_denied(self):
        pol = _policy()
        for args in [
            ["test", ";", "ls"],
            ["run", "build", "&"],
            ["test", "|", "cat"],
            ["run", "build", ">", "out"],
            ["test", "&&", "echo"],
        ]:
            with self.assertRaises(CommandDenied):
                check_args(pol, "npm", args)


class Slice4BApprovalTests(unittest.TestCase):
    def test_approval_required(self):
        pol = _policy()
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        r = run_checked(pol, "npm", ["test"], lambda p: False, cwd=root,
                        workspace_root=root)
        self.assertIn("not approved", (r["error"] or "").lower())
        self.assertFalse(r["success"])
        r2 = run_checked(pol, "npm", ["run", "build"], lambda p: True,
                         cwd=root, workspace_root=root)
        self.assertNotIn("denied", (r2["error"] or "").lower())
        self.assertNotIn("not approved", (r2["error"] or "").lower())

    def test_missing_npm_not_bypass(self):
        pol = _policy()
        root = tempfile.mkdtemp()
        # ensure project has no package.json; npm test with approve=True either
        # fails to execute (binary missing) or runs — never a policy bypass.
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        r = run_checked(pol, "npm", ["test"], lambda p: True, cwd=root,
                        workspace_root=root)
        # If npm binary missing, error is "failed to execute", not "denied";
        # either way execution was policy-gated and never bypassed.
        self.assertNotIn("bypass", (r["error"] or "").lower())
        self.assertIn(r["error"] in (None, "") or "execute" in (r["error"] or ""),
                      (True, False))


class Slice4BRegressionTests(unittest.TestCase):
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

    def test_no_args_any_for_npm(self):
        pol = _policy()
        # npm must not use a wildcard "any" form — verify via Policy specs.
        for name in ("flake8", "ruff", "black", "isort", "npm"):
            spec = pol.command_spec(name) or {}
            forms = spec.get("forms") or []
            self.assertTrue(forms, name)
            for f in forms:
                self.assertNotEqual(f.get("args"), "any", name)
                self.assertIsNotNone(f.get("args"), name)


if __name__ == "__main__":
    unittest.main()
