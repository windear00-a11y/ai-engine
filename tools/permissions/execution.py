"""Hardened, deterministic command execution primitives.

Complements (and is used by) :class:`tools.coding.exec_tools.ExecutionRunner`
with safety properties that the coding layer historically lacked:

* -c / arbitrary Python arguments are denied; only closed safe forms allowed.
* output size caps on stdout/stderr (no unbounded memory accumulation).
* process-group termination on timeout (no orphan children).
* environment filtering (strip secret-like variables), deterministic locale.
* per-command timeout ceiling.
* validated argv lists only; never shell=True; never command strings.

The execution policy is declared in :class:`tools.permissions.policy.Policy`.
This module performs policy inspection and argument validation; the actual
subprocess launch uses ``subprocess.Popen`` with streaming watched reads.
"""

import os
import signal
import subprocess
import threading
import time

from tools.permissions.policy import Policy

DEFAULT_TIMEOUT_MS = 60000
DEFAULT_STDOUT_LIMIT = 1048576
DEFAULT_STDERR_LIMIT = 1048576

# Allowed values of `-m` for python. This is the ONLY entry point.
_SAFE_PY_MODULES = frozenset({"unittest", "compileall", "py_compile"})

# Python flags that are ALWAYS denied.
_DENIED_PY_FLAGS = frozenset({"-c", "-i", "-x"})

# Forced deterministic/behaviour env (never stripped).
_LOCKED_ENV = {
    "PYTHONUNBUFFERED": "1",
    "LC_ALL": "C",
    "LANG": "C",
}


class CommandDenied(Exception):
    """Raised when a command violates policy before launch."""


# -- policy classification --------------------------------------------------

def classify_command(policy, name):
    """Return the command spec for ``name`` or raise CommandDenied."""
    spec = policy.command_spec(name)
    if spec is None:
        raise CommandDenied(f"command not allowed: {name!r}")
    return spec


def is_python_name(name):
    return name in ("python", "python3")


def check_args(policy, name, args):
    """Validate argv (without executable) against the closed policy.

    Returns a form description dict, or raises :class:`CommandDenied`.

    Allowed python forms (Phase 1A):
        python -m unittest
        python -m compileall
        python -m py_compile <validated local path>
    ``-c``, ``-i``, ``-x`` and any other flag/module are denied.
    """
    args = list(args)

    if not is_python_name(name):
        # Non-python commands must match a declared exact form.
        spec = policy.command_spec(name)
        forms = spec.get("forms", []) if spec else []
        allowed = spec.get("args_allow") if spec else None
        if allowed is not None:
            if args and allowed is True:
                return {"name": name, "args": args, "form": " ".join(args)}
            if all(a in allowed for a in args):
                return {"name": name, "args": args, "form": " ".join(args)}
            raise CommandDenied(
                f"argument not permitted for {name!r}: {args}")
        for form in forms:
            if form.get("args") == tuple(args) or form.get("args") == args:
                return {"name": name, "args": args,
                        "form": " ".join(args)}
        raise CommandDenied(f"argument form not permitted for {name!r}: {args}")

    # python
    for flag in _DENIED_PY_FLAGS:
        if flag in args:
            raise CommandDenied(f"python flag denied: {flag!r}")
    if not args:
        raise CommandDenied("bare python invocation denied")
    if args[0] != "-m":
        # Only -m is permitted; scripts, -c, etc. are denied.
        raise CommandDenied("only python -m unittest|compileall|py_compile "
                            "are allowed")
    if len(args) < 2:
        raise CommandDenied("python -m requires a module name")
    module = args[1]
    if module not in _SAFE_PY_MODULES:
        raise CommandDenied(f"python -m module denied: {module!r}")
    if module == "py_compile":
        rest = args[2:]
        if len(rest) != 1:
            raise CommandDenied(
                "py_compile requires exactly one path argument")
        if not valid_py_compile_path(rest[0]):
            raise CommandDenied("py_compile path invalid")
    return {"name": name, "args": args, "form": f"-m {module}"}


def valid_py_compile_path(arg):
    """A safe, workspace-relative-looking path argument for py_compile."""
    if not arg:
        return False
    if arg.startswith(("/", "..", "~")):
        return False
    if any(ch in arg for ch in ("\x00", ";", "&", "|", ">", "<", "*", "?")):
        return False
    if arg.startswith("-"):
        return False
    return True


# -- environment ------------------------------------------------------------

def _pattern_matches(upper_key, pattern):
    pat = pattern.upper().replace("*", "")
    return bool(pat) and pat in upper_key


def filter_env(base_environ=None, strip_patterns=None):
    """Return a filtered subprocess environment.

    Strips variables whose names contain secret-like substrings; forces
    deterministic locale and unbuffered output. Never mutates the source.
    """
    src = dict(os.environ if base_environ is None else base_environ)
    patterns = strip_patterns or [
        "*TOKEN*", "*PASSWORD*", "*SECRET*", "*PRIVATE_KEY*", "*API_KEY*",
        "*KEY*", "*AUTH*", "*CREDENTIAL*", "*PRIVATE*",
    ]
    out = {}
    for key, value in src.items():
        upper_key = key.upper()
        if any(_pattern_matches(upper_key, p) for p in patterns):
            continue
        out[key] = value
    out.update(_LOCKED_ENV)
    return out


# -- subprocess watching ----------------------------------------------------

class _OutputWatcher:
    """Watches one pipe up to a byte cap and signals overflow."""

    def __init__(self, stream, limit):
        self.stream = stream
        self.limit = limit
        self.data = bytearray()
        self.overflow = False
        self.finished = False
        self.exc = None

    def run(self):
        try:
            while True:
                chunk = self.stream.read1(65536)
                if not chunk:
                    break
                if len(self.data) + len(chunk) > self.limit:
                    # Keep only enough to reach the limit, mark overflow.
                    remaining = self.limit - len(self.data)
                    if remaining > 0:
                        self.data.extend(chunk[:remaining])
                    self.overflow = True
                    # Drain the rest to avoid blocking the child.
                    while self.stream.read(65536):
                        pass
                    break
                self.data.extend(chunk)
        except Exception as e:  # pragma: no cover - defensive
            self.exc = e
        finally:
            self.finished = True


def _decode(data, limit):
    if data is None:
        return ""
    text = bytes(data).decode("utf-8", errors="replace")
    if len(text) > limit:
        return text[:limit] + f"...[truncated]"
    return text


def _terminate_group(proc):
    """Terminate the process group and wait for it to exit."""
    if proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass


def run_checked(policy, name, args, approval_of, cwd=None,
                timeout_ms=None, stdout_limit=None, stderr_limit=None,
                environ=None, strip_patterns=None, executable=None):
    """Run a validated command under the hardened execution policy.

    Returns a result dict shaped like the coding layer's ``ExecutionRunner``:
    ``{command, executable, cwd, exit_code, stdout, stderr, duration,
    timed_out, success, error}``.

    ``approval_of`` is a callable ``(proposed_dict) -> bool`` that provides
    explicit approval for the EXECUTE domain. There is no auto-approve; launch
    only happens after approval.
    """
    args = list(args)
    try:
        form = check_args(policy, name, args)
    except CommandDenied as e:
        return {"command": " ".join([name] + args), "executable": name,
                "cwd": cwd, "exit_code": None, "stdout": "", "stderr": "",
                "duration": None, "timed_out": False, "success": False,
                "error": f"command denied: {e}"}

    spec = policy.command_spec(name) or {}
    timeout_ms = timeout_ms or spec.get("timeout_ms", DEFAULT_TIMEOUT_MS)
    stdout_limit = stdout_limit or spec.get("stdout_limit",
                                            DEFAULT_STDOUT_LIMIT)
    stderr_limit = stderr_limit or spec.get("stderr_limit",
                                            DEFAULT_STDERR_LIMIT)

    if timeout_ms <= 0:
        return {"command": " ".join([name] + args), "executable": name,
                "cwd": cwd, "exit_code": None, "stdout": "", "stderr": "",
                "duration": None, "timed_out": False, "success": False,
                "error": "timeout must be positive"}

    proposal = {"domain": "execute", "command": name, "args": args,
                "form": form["form"]}
    try:
        approved = bool(approval_of(proposal))
    except Exception:
        approved = False
    if not approved:
        return {"command": " ".join([name] + args), "executable": name,
                "cwd": cwd, "exit_code": None, "stdout": "", "stderr": "",
                "duration": None, "timed_out": False, "success": False,
                "error": "execute not approved"}

    env = filter_env(environ, strip_patterns)
    if executable is not None:
        argv = [executable] + args
    else:
        argv = [name] + args
    start = time.perf_counter()
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            shell=False, env=env, start_new_session=True,
        )
    except (FileNotFoundError, PermissionError, OSError) as e:
        duration = time.perf_counter() - start
        return {"command": " ".join(argv), "executable": name, "cwd": cwd,
                "exit_code": None, "stdout": "", "stderr": "",
                "duration": round(duration, 4), "timed_out": False,
                "success": False, "error": f"failed to execute: {e}"}

    out_w = _OutputWatcher(proc.stdout, stdout_limit)
    err_w = _OutputWatcher(proc.stderr, stderr_limit)
    t_out = threading.Thread(target=out_w.run, daemon=True)
    t_err = threading.Thread(target=err_w.run, daemon=True)
    t_out.start()
    t_err.start()

    timed_out = False
    try:
        rc = proc.wait(timeout=timeout_ms / 1000.0)
    except subprocess.TimeoutExpired:
        timed_out = True
        _terminate_group(proc)
        rc = proc.poll()
        if rc is None:
            rc = None
    finally:
        t_out.join(timeout=2)
        t_err.join(timeout=2)
        for stream in (proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass

    duration = time.perf_counter() - start
    stdout_text = _decode(out_w.data, stdout_limit)
    stderr_text = _decode(err_w.data, stderr_limit)
    if out_w.overflow:
        stdout_text += "\n[WARNING] stdout exceeded limit and was truncated"
    if err_w.overflow:
        stderr_text += "\n[WARNING] stderr exceeded limit and was truncated"

    return {
        "command": " ".join(argv),
        "executable": name,
        "cwd": str(cwd) if cwd else ".",
        "exit_code": rc,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "duration": round(duration, 4),
        "timed_out": timed_out,
        "success": (not timed_out) and (rc == 0),
        "error": "process timed out and was terminated" if timed_out else
                 ("stderr or stdout limit exceeded" if (out_w.overflow
                  or err_w.overflow) else None),
    }
