"""Slice 4D-1 tests — deterministic memory limit (512 MiB via RLIMIT_AS).

Covers:
- memory_limit_mb passed correctly (default 512, per-command override)
- RLIMIT_AS receives correct bytes
- resource import failure -> fail-closed
- setrlimit failure -> fail-closed
- existing timeout/output-cap still pass
- shell/path/approval/Python -c boundaries remain intact
- no new args="any"
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tools.permissions import Policy
from tools.permissions.execution import run_checked, check_args, CommandDenied, DEFAULT_MEMORY_LIMIT_MB


def _policy():
    return Policy()


class Slice4D1PolicyTests(unittest.TestCase):
    def test_default_memory_limit_is_512(self):
        pol = _policy()
        for name in ("python", "python3", "flake8", "ruff", "black", "isort", "npm", "pip"):
            spec = pol.command_spec(name)
            self.assertIsNotNone(spec, name)
            self.assertEqual(spec.get("memory_limit_mb"), 512, name)

    def test_per_command_override_respected(self):
        # Create custom policy with npm override 256
        data = _policy().data.copy()
        import copy
        data = copy.deepcopy(_policy().data)
        data["commands"]["npm"]["memory_limit_mb"] = 256
        pol = Policy(data)
        self.assertEqual(pol.command_spec("npm")["memory_limit_mb"], 256)
        self.assertEqual(pol.command_spec("pip")["memory_limit_mb"], 512)


class Slice4D1RlimitTests(unittest.TestCase):
    def test_rlimit_as_receives_512(self):
        pol = _policy()
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        # Capture preexec_fn via Popen mock to verify RLIMIT_AS bytes deterministically
        # Use dummy Popen to avoid actual fork/hang, just verify preexec
        # On Android, Scudo requires 12G, so effective limit is 12288
        from tools.permissions.execution import _is_android_termux
        expected_mb = 12288 if _is_android_termux() else 512
        captured = {}

        class DummyProc:
            def __init__(self, *a, **kw):
                self.stdout = open(os.devnull, "rb")
                self.stderr = open(os.devnull, "rb")
            def poll(self):
                return 0
            def wait(self, timeout=None):
                return 0

        def mock_popen(*args, **kwargs):
            captured["preexec"] = kwargs.get("preexec_fn")
            if captured["preexec"] is not None:
                with mock.patch("resource.setrlimit") as mock_set:
                    captured["preexec"]()
                    import resource
                    expected = expected_mb * 1024 * 1024
                    mock_set.assert_called_with(resource.RLIMIT_AS, (expected, expected))
            # Return dummy proc that immediately succeeds
            return DummyProc()

        with mock.patch("subprocess.Popen", side_effect=mock_popen):
            r = run_checked(pol, "python", ["-m", "compileall"], lambda p: True, cwd=root)
            self.assertIsNotNone(captured.get("preexec"), "preexec_fn not set for memory limit")
            self.assertNotIn("resource limits not supported", (r.get("error") or "").lower())
            self.assertTrue(r["success"])

    def test_per_command_override_256(self):
        import copy
        data = copy.deepcopy(_policy().data)
        data["commands"]["npm"]["memory_limit_mb"] = 256
        pol = Policy(data)
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        from tools.permissions.execution import _is_android_termux
        # On Android, effective limit is max(256, 12288) = 12288
        expected_mb = 12288 if _is_android_termux() else 256
        captured = {}

        class DummyProc:
            def __init__(self, *a, **kw):
                self.stdout = open(os.devnull, "rb")
                self.stderr = open(os.devnull, "rb")
            def poll(self):
                return 0
            def wait(self, timeout=None):
                return 0

        def mock_popen(*args, **kwargs):
            captured["preexec"] = kwargs.get("preexec_fn")
            if captured["preexec"] is not None:
                with mock.patch("resource.setrlimit") as mock_set:
                    captured["preexec"]()
                    import resource
                    expected = expected_mb * 1024 * 1024
                    mock_set.assert_called_with(resource.RLIMIT_AS, (expected, expected))
            return DummyProc()

        with mock.patch("subprocess.Popen", side_effect=mock_popen):
            r = run_checked(pol, "npm", ["test"], lambda p: True, cwd=root)
            self.assertIsNotNone(captured.get("preexec"))
            self.assertTrue(r["success"])

    def test_resource_import_failure_fail_closed(self):
        pol = _policy()
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        import builtins
        original_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "resource":
                raise ImportError("No module named 'resource'")
            return original_import(name, *args, **kwargs)

        with mock.patch("builtins.__import__", side_effect=mock_import):
            r = run_checked(pol, "python", ["-m", "compileall"], lambda p: True, cwd=root)
            self.assertIn("resource limits not supported", (r["error"] or "").lower())
            self.assertFalse(r["success"])

    def test_setrlimit_failure_fail_closed(self):
        pol = _policy()
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        # Simulate setrlimit failure via Popen preexec raising OSError
        import subprocess
        orig_popen = subprocess.Popen

        def failing_popen(*args, **kwargs):
            preexec = kwargs.get("preexec_fn")
            if preexec is not None:
                # Simulate resource.setrlimit failure inside child preexec
                raise OSError("cannot set limit: memory limit failed")
            return orig_popen(*args, **kwargs)

        with mock.patch("subprocess.Popen", side_effect=failing_popen):
            r = run_checked(pol, "python", ["-m", "compileall"], lambda p: True, cwd=root)
            self.assertIn("memory limit failed", (r["error"] or "").lower())
            self.assertFalse(r["success"])


class Slice4D1ExistingBoundaryTests(unittest.TestCase):
    def test_timeout_still_passes(self):
        # Existing test: timeout still terminates
        pol = _policy()
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        r = run_checked(pol, "python", ["-m", "compileall"], lambda p: True, cwd=root, timeout_ms=5)
        self.assertTrue(r["timed_out"])

    def test_output_cap_still(self):
        pol = _policy()
        root = tempfile.mkdtemp()
        many = os.path.join(root, "many")
        os.makedirs(many)
        for i in range(100):
            with open(os.path.join(many, f"f{i}.py"), "w") as f:
                f.write("x = %d\n" % i)
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        r = run_checked(pol, "python", ["-m", "compileall"], lambda p: True, cwd=root, stdout_limit=120)
        self.assertIn("truncated", (r["stdout"] or "").lower() + (r["stderr"] or "").lower() + (r["error"] or "").lower() or r["stdout"])

    def test_shell_path_approval_remain(self):
        pol = _policy()
        # shell/chaining denied via exact form mismatch
        with self.assertRaises(CommandDenied):
            check_args(pol, "npm", ["test", ";", "ls"])
        # path denied via workspace cwd confinement in the generic runner
        import tempfile
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        r = run_checked(pol, "npm", ["test"], lambda p: True, cwd="../escape",
                        workspace_root=root)
        self.assertIn("denied", (r["error"] or "").lower())
        # python -c still denied
        with self.assertRaises(CommandDenied):
            check_args(pol, "python", ["-c", "print(1)"])

    def test_no_new_args_any(self):
        # Policy command specs are the single source of truth; no wildcard
        # "any" forms and every allowed command has closed exact arg forms.
        pol = _policy()
        for name in ("flake8", "ruff", "black", "isort", "npm", "pip",
                     "python", "python3"):
            spec = pol.command_spec(name) or {}
            forms = spec.get("forms") or []
            self.assertTrue(forms, name)
            for f in forms:
                self.assertNotEqual(f.get("args"), "any", name)
                self.assertIsNotNone(f.get("args"), name)


if __name__ == "__main__":
    unittest.main()
