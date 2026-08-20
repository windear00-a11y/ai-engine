"""Transports for the SDK: how a request reaches the engine.

The client depends on ONE abstraction (:class:`TransportProtocol`): a plain
``execute(request) -> response`` pair of dicts, plus ``close()``. That keeps
the same :class:`~knowledge_client.client.KnowledgeClient` usable

* in-process, against the public ``api.tools.ToolInterface`` boundary
  (:class:`InProcessTransport`), or
* across a real OS process boundary, against the persistent session
  (``python -m api.session``, :class:`SessionTransport`), or
* one-shot, spawning a fresh interpreter per request
  (``python -m api.tools -``, :class:`OneShotTransport`).

No transport here imports ``sqlite3``, ``retrieval.*``, the schema, or the
CLI. In-process usage reaches the engine only through the documented public
interface.
"""

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Protocol, runtime_checkable

from knowledge_client.errors import (
    InvalidResponseError,
    KnowledgeClientError,
    TransportError,
)

__all__ = [
    "TransportProtocol",
    "InProcessTransport",
    "SessionTransport",
    "OneShotTransport",
]


def _project_root():
    """Repo root: parent of ``knowledge_client`` (one level up)."""
    return Path(__file__).resolve().parent.parent


@runtime_checkable
class TransportProtocol(Protocol):
    """Minimal transport interface the SDK depends on.

    ``execute`` accepts one request dict and returns one response dict
    conforming to the tool contract. ``close`` releases any held resources
    (a spawned session process, an in-process interface, ...).
    """

    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]: ...

    def close(self) -> None: ...


class InProcessTransport:
    """Run requests against the public engine interface in this same process.

    The engine is reached through ``api.tools.ToolInterface`` -- the
    documented public contract boundary. Construction (one of):

        InProcessTransport()                    # default database
        InProcessTransport(db_path="path.db")   # explicit database file
        InProcessTransport(interface=fixture)   # reuse an existing interface

    The ``interface`` variant is handy for tests that already own a
    ``ToolInterface`` or a stand-in object exposing ``execute``/``close``.
    """

    def __init__(self, db_path: Optional[str] = None, interface: Any = None):
        if interface is not None:
            cursor = interface  # reused; not owned by this transport
        else:
            from api.tools import ToolInterface  # public boundary, imported lazily
            if db_path is None:
                cursor = ToolInterface()  # default database
            else:
                cursor = ToolInterface(db_path=db_path)
        self._interface = cursor
        self._closed = False

    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        if self._closed:
            raise TransportError("in-process transport is closed")
        try:
            return self._interface.execute(request)
        except KnowledgeClientError:
            raise
        except Exception as exc:  # noqa: BLE001 - surface as transport failure
            raise TransportError("in-process interface failed: %s" % (exc,)) from exc

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        closer = getattr(self._interface, "close", None)
        if closer is not None:
            try:
                closer()
            except Exception:  # noqa: BLE001 - close must not raise to the caller
                pass


class SessionTransport:
    """Persistent session transport via ``python -m api.session``.

    One session process stays alive across many requests (the repository is
    loaded once, so each request after the first is cheap). Protocol:

    * one JSON request per line on the process stdin,
    * one JSON response per line on stdout,
    * EOF on stdin terminates the session cleanly.

    Transport failures (dead process, non-JSON output, I/O errors) raise
    :class:`TransportError`.
    """

    def __init__(self, db: Optional[str] = None, python: Optional[str] = None,
                 root: Optional[str] = None, wait_timeout: int = 120):
        self.db = db
        self.python = python or sys.executable
        self.root = str(root or _project_root())
        cmd = [self.python, "-m", "api.session"]
        if self.db:
            cmd.extend(["--db", self.db])
        try:
            self._proc = subprocess.Popen(
                cmd, cwd=self.root,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1)
        except OSError as exc:
            raise TransportError(
                "could not spawn session process: %s" % (exc,)) from exc
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        assert self._proc.stderr is not None
        self._wait_timeout = wait_timeout
        self._closed = False
        self._stderr_lines = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        if self._closed:
            raise TransportError("session is closed")
        try:
            payload_text = json.dumps(request)
        except (TypeError, ValueError) as exc:
            raise TransportError(
                "request is not JSON-serializable: %s" % exc) from exc
        try:
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None
            self._proc.stdin.write(payload_text + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
        except (OSError, ValueError) as exc:
            raise TransportError("session write/read failed: %s" % exc) from exc
        if not line:
            self._closed = True
            raise TransportError(
                "session ended unexpectedly (EOF); stderr: %s"
                % "".join(self._stderr_lines)[:300])
        try:
            payload = json.loads(line)
        except ValueError:
            raise InvalidResponseError(
                "session emitted a non-JSON line: %r" % (line[:200],))
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise InvalidResponseError(
                "session emitted a non-conforming envelope: %r" % (payload,))
        return payload

    def close(self) -> None:
        if getattr(self, "_pipes_closed", False):
            return
        self._closed = True
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.close()  # EOF -> clean session shutdown
        except (OSError, ValueError):
            pass
        try:
            self._proc.wait(timeout=self._wait_timeout)
        except subprocess.TimeoutExpired:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        for attr in ("stdout", "stderr"):
            handle = getattr(self._proc, attr, None)
            if handle is not None:
                try:
                    handle.close()
                except (OSError, ValueError):
                    pass
        self._pipes_closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False

    def _drain_stderr(self):
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            self._stderr_lines.append(line)

    def session_stats(self):
        """Parse the session's shutdown stats line (stderr), or None."""
        for line in self._stderr_lines:
            if line.startswith("SESSION stats=") and line.endswith(" end\n"):
                raw = line[len("SESSION stats="):-len(" end\n")].strip()
                try:
                    return json.loads(raw)
                except ValueError:
                    return None
        return None


class OneShotTransport:
    """One fresh interpreter per request via ``python -m api.tools -``.

    Simple and isolated, but pays the full repository-load cost on every
    call. Use :class:`SessionTransport` when making many requests.
    """

    def __init__(self, db: Optional[str] = None, python: Optional[str] = None,
                 root: Optional[str] = None, timeout: int = 180):
        self.db = db
        self.python = python or sys.executable
        self.root = str(root or _project_root())
        self.timeout = timeout

    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        cmd = [self.python, "-m", "api.tools", "-"]
        if self.db:
            cmd.extend(["--db", self.db])
        try:
            payload_text = json.dumps(request)
        except (TypeError, ValueError) as exc:
            raise TransportError(
                "request is not JSON-serializable: %s" % exc) from exc
        try:
            proc = subprocess.run(
                cmd, cwd=self.root, input=payload_text,
                capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TransportError("runner failed: %s" % exc) from exc
        payload = self._parse_single_json(proc.stdout)
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise InvalidResponseError(
                "runner emitted a non-conforming envelope: %r" % (payload,))
        expected_rc = 0 if payload["ok"] else 1
        if proc.returncode != expected_rc:
            raise TransportError(
                "runner exit code %d does not match response ok=%r (stderr: %s)"
                % (proc.returncode, payload["ok"], (proc.stderr or "")[:200]))
        return payload

    @staticmethod
    def _parse_single_json(text):
        stripped = text.strip()
        if not stripped:
            raise InvalidResponseError("runner produced no output")
        try:
            payload, end = json.JSONDecoder().raw_decode(stripped)
        except ValueError:
            raise InvalidResponseError(
                "runner produced invalid JSON: %r" % (text[:200],))
        if stripped[end:].strip():
            raise InvalidResponseError(
                "runner produced more than one JSON document")
        return payload

    def close(self) -> None:
        pass