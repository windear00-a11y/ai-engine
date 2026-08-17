"""Controlled Execution + Verification Layer.

This layer lets the engine build, test, and statically verify projects in a
controlled, deterministic, model-independent way. It does NOT expose an
unrestricted shell: every process is run through :class:`ExecutionRunner`, which
only executes explicitly allowlisted executables with explicitly validated
arguments, from a workspace-bound working directory, under a timeout.

Safety properties
------------------
* No ``shell=True`` and no command-string interpolation.
* Executable and arguments are validated separately against an allowlist.
* ``cwd`` is resolved through :class:`tools.coding.fs.Workspace`, so it can never
  escape the workspace root.
* A configurable timeout safely terminates runaway processes.
* Children run with ``PYTHONPYCACHEPREFIX`` pointing at a runner-owned temp dir,
  so verification never writes ``.pyc`` artifacts into the workspace.
* Failures are never silently swallowed: exit code, stdout, stderr and an
  ``error`` field are always reported.

The execution layer is intentionally thin and dependency-light.
"""

import json
import os
import subprocess
import sys
import tempfile
import time

from .fs import Workspace, PathError


# --------------------------------------------------------------------------
# Execution runner
# --------------------------------------------------------------------------

class ExecutionRunner:
    """Run only explicitly allowlisted commands with validated arguments."""

    def __init__(self, root, allowlist=None, default_timeout=30):
        self.ws = Workspace(root)
        self.allowlist = allowlist if allowlist is not None else self._default_allowlist()
        self.default_timeout = default_timeout
        # Keep Python bytecode out of the workspace during verification.
        self._pycache = tempfile.mkdtemp(prefix="kb_pycache_")

    @staticmethod
    def _default_allowlist():
        # Minimal safe default: only the active Python interpreter.
        return {
            "python": {"executable": sys.executable, "args": "any"},
            "python3": {"executable": sys.executable, "args": "any"},
        }

    def is_allowed(self, name):
        return name in self.allowlist

    @staticmethod
    def _err(name, args, executable, cwd, message):
        argv = ([executable] if executable else [name]) + list(args)
        return {
            "command": " ".join(argv),
            "executable": name,
            "cwd": cwd,
            "exit_code": None,
            "stdout": "",
            "stderr": "",
            "duration": None,
            "timed_out": False,
            "success": False,
            "error": message,
        }

    def run(self, name, args=None, cwd=None, timeout=None):
        args = list(args) if args else []

        spec = self.allowlist.get(name)
        if spec is None:
            return self._err(name, args, None, cwd,
                             f"executable not allowed: {name!r}")
        executable = spec.get("executable") or name
        policy = spec.get("args", "any")
        if policy != "any":
            allowed = set(policy)
            for a in args:
                if a not in allowed:
                    return self._err(name, args, executable, cwd,
                                     f"argument not allowed: {a!r}")

        if cwd is None:
            cwd_abs = self.ws.root
        else:
            try:
                cwd_abs = self.ws.resolve(cwd)
            except PathError as e:
                return self._err(name, args, executable, cwd, str(e))
        if not os.path.isdir(cwd_abs):
            return self._err(name, args, executable, cwd,
                             f"cwd is not a directory: {cwd!r}")

        timeout = timeout if timeout is not None else self.default_timeout
        argv = [executable] + args
        env = dict(os.environ)
        env["PYTHONPYCACHEPREFIX"] = self._pycache

        start = time.perf_counter()
        try:
            proc = subprocess.run(
                argv, cwd=cwd_abs, timeout=timeout, capture_output=True,
                text=True, shell=False, check=False, env=env,
            )
        except subprocess.TimeoutExpired as e:
            duration = time.perf_counter() - start
            stdout = e.stdout if isinstance(e.stdout, str) else ""
            stderr = e.stderr if isinstance(e.stderr, str) else ""
            return {
                "command": " ".join(argv),
                "executable": name,
                "cwd": self.ws.rel(cwd_abs),
                "exit_code": None,
                "stdout": stdout,
                "stderr": stderr,
                "duration": round(duration, 4),
                "timed_out": True,
                "success": False,
                "error": "process timed out and was terminated",
            }
        except (FileNotFoundError, PermissionError, OSError) as e:
            duration = time.perf_counter() - start
            return {
                "command": " ".join(argv),
                "executable": name,
                "cwd": self.ws.rel(cwd_abs),
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "duration": round(duration, 4),
                "timed_out": False,
                "success": False,
                "error": f"failed to execute: {e}",
            }

        duration = time.perf_counter() - start
        return {
            "command": " ".join(argv),
            "executable": name,
            "cwd": self.ws.rel(cwd_abs),
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "duration": round(duration, 4),
            "timed_out": False,
            "success": proc.returncode == 0,
            "error": None,
        }


# --------------------------------------------------------------------------
# Project verification tools
# --------------------------------------------------------------------------

class ProjectVerificationTools:
    """Deterministic project test/build/check using an allowlisted runner."""

    def __init__(self, root, runner=None):
        self.runner = runner or ExecutionRunner(root)

    # -- detection helpers ------------------------------------------------

    def _walk_py(self, root):
        for _, _, files in os.walk(root):
            for f in files:
                if f.endswith(".py"):
                    return True
        return False

    def _has_tests_dir(self, root):
        for current, dirs, _ in os.walk(root):
            if "tests" in dirs or "test" in dirs:
                return True
        return False

    def _read_json(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def detect_test_config(self):
        root = self.runner.ws.root
        pkg = os.path.join(root, "package.json")
        if os.path.isfile(pkg):
            data = self._read_json(pkg)
            if isinstance(data, dict):
                scripts = data.get("scripts", {})
                if isinstance(scripts, dict) and "test" in scripts:
                    return {"type": "node",
                            "command_name": "npm",
                            "args": ["test"],
                            "command": "npm test"}
        if (self._walk_py(root) or self._has_tests_dir(root)
                or any(os.path.isfile(os.path.join(root, n))
                       for n in ("pytest.ini", "pyproject.toml",
                                 "setup.py", "setup.cfg", "tox.ini"))):
            return {"type": "python",
                    "command_name": "python",
                    "args": ["-m", "unittest", "discover", "-s", ".", "-t", "."],
                    "command": "python -m unittest discover -s . -t ."}
        return None

    def detect_build_config(self):
        root = self.runner.ws.root
        pkg = os.path.join(root, "package.json")
        if os.path.isfile(pkg):
            data = self._read_json(pkg)
            if isinstance(data, dict):
                scripts = data.get("scripts", {})
                if isinstance(scripts, dict) and "build" in scripts:
                    return {"type": "node",
                            "command_name": "npm",
                            "args": ["run", "build"],
                            "command": "npm run build"}
        if self._walk_py(root):
            return {"type": "python",
                    "command_name": "python",
                    "args": ["-m", "compileall", "-q", "."],
                    "command": "python -m compileall -q ."}
        return None

    # -- public tools -----------------------------------------------------

    def project_test(self):
        root = self.runner.ws.root
        cfg = self.detect_test_config()
        if cfg is None:
            return {"root": root, "detected": None, "command": None,
                    "exit_code": None, "stdout": "", "stderr": "",
                    "duration": None, "timed_out": False, "success": False,
                    "error": "no supported test configuration detected"}
        if not self.runner.is_allowed(cfg["command_name"]):
            return {"root": root, "detected": cfg["command"], "command": None,
                    "exit_code": None, "stdout": "", "stderr": "",
                    "duration": None, "timed_out": False, "success": False,
                    "error": f"test command not allowed: {cfg['command_name']!r}"}
        res = self.runner.run(cfg["command_name"], cfg["args"], cwd=".")
        res["root"] = root
        res["detected"] = cfg["command"]
        return res

    def project_build(self):
        root = self.runner.ws.root
        cfg = self.detect_build_config()
        if cfg is None:
            return {"root": root, "detected": None, "command": None,
                    "exit_code": None, "stdout": "", "stderr": "",
                    "duration": None, "timed_out": False, "success": False,
                    "error": "no supported build configuration detected"}
        if not self.runner.is_allowed(cfg["command_name"]):
            return {"root": root, "detected": cfg["command"], "command": None,
                    "exit_code": None, "stdout": "", "stderr": "",
                    "duration": None, "timed_out": False, "success": False,
                    "error": f"build command not allowed: {cfg['command_name']!r}"}
        res = self.runner.run(cfg["command_name"], cfg["args"], cwd=".")
        res["root"] = root
        res["detected"] = cfg["command"]
        return res

    def project_check(self):
        """Static, deterministic checks (no network, no arbitrary execution)."""
        root = self.runner.ws.root
        checks = []

        # Python syntax compilation (in-process; writes nothing).
        py_files = []
        for current, _, files in os.walk(root):
            for f in files:
                if f.endswith(".py"):
                    py_files.append(os.path.join(current, f))
        syntax_ok = True
        syntax_detail = []
        for path in py_files:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    compile(fh.read(), path, "exec")
            except SyntaxError as e:
                syntax_ok = False
                syntax_detail.append(f"{path}:{e.lineno}: {e.msg}")
            except (OSError, ValueError) as e:
                syntax_ok = False
                syntax_detail.append(f"{path}: {e}")
        checks.append({
            "name": "python_syntax",
            "status": "ok" if syntax_ok else "fail",
            "detail": "all .py files compile" if syntax_ok
                      else "; ".join(syntax_detail),
        })

        # JavaScript/TypeScript configuration detection.
        pkg_path = os.path.join(root, "package.json")
        if os.path.isfile(pkg_path):
            data = self._read_json(pkg_path)
            if isinstance(data, dict):
                missing = []
                main = data.get("main")
                if isinstance(main, str) and not os.path.isfile(os.path.join(root, main)):
                    missing.append(f"main references missing file: {main}")
                bin_ = data.get("bin")
                if isinstance(bin_, dict):
                    for target in bin_.values():
                        if isinstance(target, str) and not os.path.isfile(
                                os.path.join(root, target)):
                            missing.append(f"bin references missing file: {target}")
                checks.append({
                    "name": "js_config_references",
                    "status": "ok" if not missing else "fail",
                    "detail": "package.json references resolve"
                              if not missing else "; ".join(missing),
                })
            else:
                checks.append({
                    "name": "js_config_references",
                    "status": "fail",
                    "detail": "package.json is not valid JSON",
                })
            if os.path.isfile(os.path.join(root, "tsconfig.json")):
                checks.append({
                    "name": "ts_config",
                    "status": "ok",
                    "detail": "tsconfig.json detected",
                })

        success = all(c["status"] == "ok" for c in checks) if checks else True
        return {
            "root": root,
            "checks": checks,
            "success": success,
            "error": None,
        }
