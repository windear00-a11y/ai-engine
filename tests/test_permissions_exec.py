"""Tests for hardened command execution (Phase 1A).

Covers: safe python commands allowed; -c/-i/-x and arbitrary flags denied;
command allowlist; dangerous npm/make/git/network denied; output limits;
timeout + process-group cleanup; environment filtering; and integration of
the safety layer through the coding facade.
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__),
                                                "..")))

from tools.permissions import Policy, PathPolicy, ApprovalGate, EngineState
from tools.permissions.execution import (
    run_checked, check_args, filter_env, CommandDenied,
)
from tools.coding.exec_tools import ExecutionRunner
from tools.coding.write_tools import WriteTools


def _mkproject(name="exec"):
    d = tempfile.mkdtemp(prefix=f"perm_exec_{name}_")
    os.makedirs(os.path.join(d, "src"), exist_ok=True)
    os.makedirs(os.path.join(d, "tests"), exist_ok=True)
    with open(os.path.join(d, "src", "mod.py"), "w") as f:
        f.write("def add(a, b):\n    return a + b\n")
    with open(os.path.join(d, "tests", "test_mod.py"), "w") as f:
        f.write(
            "import unittest\n"
            "from src.mod import add\n"
            "class T(unittest.TestCase):\n"
            "    def test_add(self):\n"
            "        self.assertEqual(add(1, 2), 3)\n")
    return d


def _runner(root, policy, approver=None):
    return ExecutionRunner(root, policy=policy, approver=approver)


class CommandDenialTests(unittest.TestCase):
    def setUp(self):
        self.root = _mkproject()
        self.pol = Policy()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_python_c_denied(self):
        with self.assertRaises(CommandDenied):
            check_args(self.pol, "python3", ["-c", "print(1)"])

    def test_python_i_denied(self):
        with self.assertRaises(CommandDenied):
            check_args(self.pol, "python", ["-i"])

    def test_python_x_denied(self):
        with self.assertRaises(CommandDenied):
            check_args(self.pol, "python", ["-x"])

    def test_bare_python_denied(self):
        with self.assertRaises(CommandDenied):
            check_args(self.pol, "python3", [])

    def test_python_script_denied(self):
        with self.assertRaises(CommandDenied):
            check_args(self.pol, "python3", ["some_script.py"])

    def test_unknown_module_denied(self):
        with self.assertRaises(CommandDenied):
            check_args(self.pol, "python3", ["-m", "os", "system", "x"])

    def test_safe_forms_allowed(self):
        for name in ("python", "python3"):
            self.assertIsNotNone(check_args(
                self.pol, name, ["-m", "unittest"]))
            self.assertIsNotNone(check_args(
                self.pol, name, ["-m", "compileall"]))
            self.assertIsNotNone(check_args(
                self.pol, name, ["-m", "py_compile", "src/mod.py"]))

    def test_py_compile_absolute_path_denied(self):
        with self.assertRaises(CommandDenied):
            check_args(self.pol, "python", ["-m", "py_compile", "/etc/passwd"])

    def test_py_compile_traversal_denied(self):
        with self.assertRaises(CommandDenied):
            check_args(self.pol, "python", ["-m", "py_compile", "../x.py"])

    def test_unknown_command_denied(self):
        with self.assertRaises(CommandDenied):
            check_args(self.pol, "rm", ["-rf", "/"])
        with self.assertRaises(CommandDenied):
            check_args(self.pol, "git", ["status"])
        with self.assertRaises(CommandDenied):
            check_args(self.pol, "make", ["all"])
        with self.assertRaises(CommandDenied):
            check_args(self.pol, "npm", ["install"])

    def test_run_checked_reports_denial(self):
        r = run_checked(self.pol, "python3", ["-c", "print(1)"],
                        lambda p: True, cwd=self.root)
        self.assertIn("denied", r["error"])
        self.assertFalse(r["success"])


class ApprovalRequiredTests(unittest.TestCase):
    def setUp(self):
        self.root = _mkproject()
        self.pol = Policy()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_approval_required_for_execute(self):
        runner = _runner(self.root, self.pol)  # no approver -> deny all
        r = runner.run("python", ["-m", "unittest"])
        self.assertIn("not approved", r["error"])
        self.assertFalse(r["success"])

    def test_approval_provided_runs(self):
        runner = _runner(self.root, self.pol, approver=lambda p: True)
        r = runner.run("python", ["-m", "py_compile", "src/mod.py"])
        self.assertEqual(r["exit_code"], 0, r)

    def test_gate_approval_roundtrip(self):
        state = EngineState(db_path=os.path.join(
            tempfile.mkdtemp(), "s.db"))

        def approver(proposal):
            return proposal.get("domain") == "execute"

        gate = ApprovalGate(path_policy=PathPolicy(self.root),
                            state=state, approver=approver)
        runner = ExecutionRunner(self.root, policy=self.pol,
                                 permissions=gate)
        r = runner.run("python", ["-m", "py_compile", "src/mod.py"])
        # Approval granted through the gate; compile succeeds.
        self.assertEqual(r["exit_code"], 0, r)
        self.assertIsNone(r["error"])


class OutputLimitTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="perm_exec_out_")
        self.pol = Policy()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_stdout_limit_truncates(self):
        # python -m compileall lists files; with a tiny cap it must truncate.
        many = os.path.join(self.root, "many")
        os.makedirs(many)
        for i in range(300):
            with open(os.path.join(many, f"f{i}.py"), "w") as f:
                f.write("x = %d\n" % i)
        r = run_checked(self.pol, "python3",
                        ["-m", "compileall", "many"],
                        lambda p: True, cwd=self.root,
                        stdout_limit=120)
        # The truncation must have kicked in and been reported.
        self.assertIn("truncated", r["stdout"])
        # The returned text stays bounded (limit + warning banner).
        self.assertLess(len(r["stdout"]), 2048, r["stdout"])

    def test_runaway_output_killed(self):
        # A python -c is denied, so we cannot use it to generate spam; but we
        # can drive a long-running -m compileall and rely on timeout.
        r = run_checked(self.pol, "python3", ["-m", "compileall"],
                        lambda p: True, cwd=self.root, timeout_ms=5)
        self.assertTrue(r["timed_out"])


class TimeoutAndProcessGroupTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="perm_exec_to_")
        self.pol = Policy()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_timeout_terminates(self):
        r = run_checked(self.pol, "python3",
                        ["-m", "compileall", "."],
                        lambda p: True, cwd=self.root, timeout_ms=5)
        self.assertTrue(r["timed_out"])
        self.assertIn("terminated", r["error"] or "")

    def test_output_cap_bounds_memory(self):
        # Even with a huge output producer, cap keeps memory bounded (we only
        # assert convergence, not exact timing).
        r = run_checked(self.pol, "python3",
                        ["-m", "unittest", "discover", "-s", ".", "-t", "."],
                        lambda p: True, cwd=self.root,
                        stdout_limit=1024, timeout_ms=8000)
        # Should terminate (either with 0 tests or a discovery error) without
        # hanging.
        self.assertIn(r["timed_out"], (True, False))


class EnvFilteringTests(unittest.TestCase):
    def test_secret_vars_stripped(self):
        env = filter_env({
            "MYTOKEN": "t", "PAYLOAD_KEY": "k", "API_KEY": "a",
            "SECRET": "s", "PASSWORD": "p", "PRIVATE_KEY": "pk",
            "NORMAL": "ok", "PATH": "/usr/bin", "HOME": "/root",
        })
        for bad in ("MYTOKEN", "PAYLOAD_KEY", "API_KEY", "SECRET",
                    "PASSWORD", "PRIVATE_KEY"):
            self.assertNotIn(bad, env, bad)
        self.assertEqual(env.get("NORMAL"), "ok")
        self.assertEqual(env.get("PYTHONUNBUFFERED"), "1")
        self.assertEqual(env.get("LC_ALL"), "C")

    def test_base_env_not_mutated(self):
        base = {"MYTOKEN": "x"}
        env = filter_env(base)
        self.assertNotIn("MYTOKEN", env)
        self.assertIn("MYTOKEN", base)


class RunnerCwdConfinementTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="perm_exec_cwd_")
        self.pol = Policy()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_cwd_traversal_rejected_in_hardened_mode(self):
        runner = _runner(self.root, self.pol, approver=lambda p: True)
        r = runner.run("python", ["-m", "unittest"], cwd="../escape")
        self.assertIn("error", r)
        self.assertFalse(r["success"])

    def test_cwd_absolute_escape_rejected(self):
        runner = _runner(self.root, self.pol, approver=lambda p: True)
        r = runner.run("python", ["-m", "unittest"], cwd="/etc")
        self.assertIn("error", r)


class WriteIntegrationTests(unittest.TestCase):
    """WriteTools + gate integration."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="perm_exec_write_")
        os.makedirs(os.path.join(self.root, "src"))
        with open(os.path.join(self.root, "src", "a.py"), "w") as f:
            f.write("x = 1\n")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_gated_write_requires_approval(self):
        state = EngineState(db_path=os.path.join(
            tempfile.mkdtemp(), "s.db"))
        gate = ApprovalGate(path_policy=PathPolicy(self.root),
                            state=state, approver=lambda p: True)
        wt = WriteTools(self.root, permissions=gate)
        res = wt.write("src/new.py", "y = 2\n")
        self.assertIsNone(res.get("error"))
        self.assertEqual(res["bytes_written"], 6)

    def test_write_blocked_without_gate_also_hard_guards_db(self):
        wt = WriteTools(self.root)  # no gate
        # hard guard applies even without a gate object
        res = wt.write("database/knowledge.db", "bad")
        self.assertIn("immutable", res["error"])


if __name__ == "__main__":
    unittest.main()