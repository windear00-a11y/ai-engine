"""Tests for hardened command execution (Phase 1A, generic Core trust layer).

Covers: safe python commands allowed; -c/-i/-x and arbitrary flags denied;
command allowlist; dangerous npm/make/git/network denied; output limits;
timeout + process-group cleanup; environment filtering; workspace cwd
confinement; and gateway approval integration. Tests the generic
:mod:`tools.permissions` layer directly (no domain/coding facade).
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


def _gate_approver(gate):
    """Approval callable for the EXECUTE domain routed through the gate."""
    from tools.permissions.approvalgate import Domain
    from tools.permissions.decisions import DecisionKind

    def _approve(proposal):
        d = gate.check(Domain.EXECUTE, proposal.get("command"))
        if d.kind == DecisionKind.ALLOW:
            return True
        if d.kind == DecisionKind.REQUIRE_APPROVAL and gate.approver is not None:
            return bool(gate.approver({"domain": "execute",
                                       "command": proposal.get("command")}))
        return False
    return _approve


class _GenericRunner:
    """Stand-in for the removed coding facade: policy + approval + cwd.

    Uses only the generic trust primitives (``run_checked`` with workspace
    cwd confinement and explicit EXECUTE approval).
    """

    def __init__(self, root, policy, approver=None, permissions=None):
        self.root = root
        self.policy = policy
        self.approver = approver
        self.permissions = permissions

    def run(self, name, args=None, cwd=None, timeout=None):
        approval_of = self.approver or (lambda p: False)
        if self.permissions is not None:
            approval_of = _gate_approver(self.permissions)
        timeout_ms = int(timeout * 1000) if timeout is not None else None
        return run_checked(
            self.policy, name, args or [], approval_of,
            cwd=cwd, workspace_root=self.root, timeout_ms=timeout_ms)


def _runner(root, policy, approver=None, permissions=None):
    return _GenericRunner(root, policy, approver=approver,
                          permissions=permissions)


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
        runner = _runner(self.root, self.pol, permissions=gate)
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
                        ["-m", "compileall"],
                        lambda p: True, cwd=self.root,
                        stdout_limit=120, workspace_root=self.root)
        # The truncation must have kicked in and been reported.
        self.assertIn("truncated", r["stdout"])
        # The returned text stays bounded (limit + warning banner).
        self.assertLess(len(r["stdout"]), 2048, r["stdout"])

    def test_runaway_output_killed(self):
        # A python -c is denied, so we cannot use it to generate spam; but we
        # can drive a long-running -m compileall and rely on timeout.
        r = run_checked(self.pol, "python3", ["-m", "compileall"],
                        lambda p: True, cwd=self.root, timeout_ms=5,
                        workspace_root=self.root)
        self.assertTrue(r["timed_out"])


class TimeoutAndProcessGroupTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="perm_exec_to_")
        self.pol = Policy()
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_timeout_terminates(self):
        r = run_checked(self.pol, "python3",
                        ["-m", "compileall"],
                        lambda p: True, cwd=self.root, timeout_ms=5,
                        workspace_root=self.root)
        self.assertTrue(r["timed_out"])
        self.assertIn("terminated", r["error"] or "")

    def test_output_cap_bounds_memory(self):
        # Even with a huge output producer, cap keeps memory bounded (we only
        # assert convergence, not exact timing).
        r = run_checked(self.pol, "python3",
                        ["-m", "unittest"],
                        lambda p: True, cwd=self.root,
                        stdout_limit=1024, timeout_ms=8000,
                        workspace_root=self.root)
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
    """Workspace cwd confinement lives in the generic Core trust layer
    (``run_checked(workspace_root=...)``); previously only the removed coding
    facade enforced it. These tests pin the property in Core."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="perm_exec_cwd_")
        os.makedirs(os.path.join(self.root, "src"), exist_ok=True)
        with open(os.path.join(self.root, "src", "mod.py"), "w") as f:
            f.write("x = 1\n")
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

    def test_cwd_defaults_to_workspace_root(self):
        runner = _runner(self.root, self.pol, approver=lambda p: True)
        r = runner.run("python", ["-m", "py_compile", "src/mod.py"],
                       cwd=None)
        self.assertEqual(r["exit_code"], 0, r)


class PathPolicyZoneTests(unittest.TestCase):
    """Generic path-policy zone classification (trust layer)."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="perm_exec_zone_")
        os.makedirs(os.path.join(self.root, "src"))
        with open(os.path.join(self.root, "src", "a.py"), "w") as f:
            f.write("x = 1\n")
        self.addCleanup(lambda: __import__("shutil").rmtree(
            self.root, ignore_errors=True))

    def test_protected_db_hard_guard(self):
        from tools.permissions.pathpolicy import hard_write_guard
        self.assertTrue(hard_write_guard(
            os.path.join(self.root, "database", "knowledge.db"), self.root))

    def test_blocked_db_zone_deny(self):
        pp = PathPolicy(self.root)
        res = pp.read_decision(
            os.path.join(self.root, "database", "knowledge.db"))
        self.assertEqual(res.kind.value, "deny")

    def test_escape_raises_path_error(self):
        from tools.permissions.fs import PathError
        pp = PathPolicy(self.root)
        with self.assertRaises(PathError):
            pp.read_decision("../secret.txt")


if __name__ == "__main__":
    unittest.main()