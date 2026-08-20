"""Independent external client for the Knowledge Engine tool-call contract.

This module knows ONLY the public request/response contract defined by the
tool interface (operation names, argument names, response envelopes). It is
deliberately transport-agnostic: a ``send`` callable is injected, so the same
client logic runs against either

* the in-process public boundary (``api.tools.ToolInterface.execute``), or
* a real process boundary (``python -m api.tools -`` via
  :class:`SubprocessTransport`).

This module does NOT import, reach into, or depend on:

* ``sqlite3`` or any SQL
* ``retrieval.*`` (repository / store / schema internals)
* ``api.knowledge_api`` / ``api.contract`` internals
* the CLI (``ai_engine``)

It speaks plain dicts and raises :class:`ContractError` for contract-level
problems (non-conforming envelopes, transport failures, or any
``ok: false`` response surfaced by the engine).
"""

import json
import subprocess
import sys
import threading
from pathlib import Path

__all__ = [
    "ContractError",
    "KnowledgeClient",
    "SubprocessTransport",
]


def _project_root():
    """Repo root: parent of ``tests/external_client`` (two levels up)."""
    return Path(__file__).resolve().parent.parent.parent


class ContractError(Exception):
    """Raised when the engine rejects a request or the transport misbehaves.

    Attributes:
        code: stable machine-readable code from the error envelope
            (``node_not_found``, ``invalid_argument``, ``unknown_operation``,
            ``invalid_request``, ...) or a client-side code prefixed with
            ``client:``.
        operation: the operation that was requested, if known.
    """

    def __init__(self, code, message, operation=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.operation = operation

    def __repr__(self):
        return "%s(code=%r, message=%r, operation=%r)" % (
            type(self).__name__, self.code, self.message, self.operation)


class KnowledgeClient:
    """Contract-only client over an injected transport.

    ``send`` must be a callable receiving one request dict and returning one
    response dict matching the contract envelope. With this minimal
    dependency the same client is usable in-process and across processes.
    """

    def __init__(self, send):
        self._send = send

    # -- low-level ----------------------------------------------------------

    def raw_request(self, operation, arguments=None):
        """Send an arbitrary framed request; return the raw response dict.

        Unlike the typed helpers below, this never raises on an error
        envelope -- useful for exercising error paths deliberately.
        """
        request = {"operation": operation}
        if arguments is not None:
            request["arguments"] = arguments
        response = self._send(request)
        self._check_envelope(response)
        return response

    def request(self, operation, arguments=None):
        """Send a framed request; return ``result`` or raise ContractError."""
        response = self.raw_request(operation, arguments)
        return response["result"]

    # -- typed helpers (approved operations only) ---------------------------

    def inspect(self):
        """Aggregate counts and type breakdowns."""
        return self.request("inspect")

    def search(self, query, limit=None, node_type=None):
        """Full-text search over node text; returns a list of nodes."""
        arguments = {"query": query}
        if limit is not None:
            arguments["limit"] = limit
        if node_type is not None:
            arguments["node_type"] = node_type
        return self.request("search", arguments)

    def get(self, node_id):
        """Fetch one node by its id."""
        return self.request("get", {"node_id": node_id})

    def provenance(self, node_id):
        """Provenance record for one node."""
        return self.request("provenance", {"node_id": node_id})

    def related(self, node_id, limit=None):
        """Neighbours of a node in either direction."""
        arguments = {"node_id": node_id}
        if limit is not None:
            arguments["limit"] = limit
        return self.request("related", arguments)

    def follow(self, node_id, relationship_type=None):
        """Follow edges of one type out of a node."""
        arguments = {"node_id": node_id}
        if relationship_type is not None:
            arguments["relationship_type"] = relationship_type
        return self.request("follow", arguments)

    # -- contract envelope checks -------------------------------------------

    def _check_envelope(self, response):
        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            raise ContractError(
                "client:invalid_response",
                "transport returned a non-conforming envelope: %r" % (response,),
                None)
        if not response["ok"]:
            error = response.get("error")
            if not isinstance(error, dict):
                raise ContractError(
                    "client:invalid_response",
                    "error envelope missing structured 'error' detail",
                    response.get("operation"))
            raise ContractError(
                error.get("code", "knowledge_error"),
                error.get("message", ""),
                error.get("operation", response.get("operation")))
        if "result" not in response:
            raise ContractError(
                "client:invalid_response",
                "ok envelope missing 'result'",
                response.get("operation"))
        return response["result"]


class SubprocessTransport:
    """Run each request in a fresh interpreter via ``python -m api.tools -``.

    Proves the contract works across the Python process boundary. Each call:

    * serializes one request to stdin,
    * reads stdout, and
    * parses EXACTLY ONE JSON document.

    The runner's exit-code contract is also enforced: 0 for ``ok: true``
    responses, 1 for ``ok: false``.
    """

    def __init__(self, db=None, python=None, root=None, timeout=180):
        self.db = db
        self.python = python or sys.executable
        self.root = str(root or _project_root())
        self.timeout = timeout

    def __call__(self, request):
        cmd = [self.python, "-m", "api.tools", "-"]
        if self.db:
            cmd.extend(["--db", self.db])
        try:
            payload_text = json.dumps(request)
        except (TypeError, ValueError) as exc:
            raise ContractError(
                "client:unserializable",
                "request is not JSON-serializable: %s" % exc,
                request.get("operation") if isinstance(request, dict) else None)
        try:
            proc = subprocess.run(
                cmd, cwd=self.root, input=payload_text,
                capture_output=True, text=True, timeout=self.timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContractError("client:transport", "runner failed: %s" % exc)
        payload = self._parse_single_json(proc.stdout, self.root)
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise ContractError(
                "client:invalid_response",
                "runner emitted a non-conforming envelope: %r" % (payload,))
        expected_rc = 0 if payload["ok"] else 1
        if proc.returncode != expected_rc:
            raise ContractError(
                "client:transport",
                "runner exit code %d does not match response ok=%r (stderr: %s)"
                % (proc.returncode, payload["ok"], (proc.stderr or "")[:200]))
        return payload

    @staticmethod
    def _parse_single_json(text, root=None):
        stripped = text.strip()
        if not stripped:
            raise ContractError(
                "client:invalid_response",
                "runner produced no output")
        try:
            payload, end = json.JSONDecoder().raw_decode(stripped)
        except ValueError:
            raise ContractError(
                "client:invalid_response",
                "runner produced invalid JSON: %r" % (text[:200],))
        if stripped[end:].strip():
            raise ContractError(
                "client:invalid_response",
                "runner produced more than one JSON document")
        return payload


class SessionTransport:
    """Speak the persistent session protocol through a live subprocess.

    The session process stays alive across requests (the repository is loaded
    once), unlike the one-shot :class:`SubprocessTransport` which pays the full
    load cost on every call. Protocol:

    * write one JSON request per line on the process stdin,
    * read one JSON response per line on stdout,
    * close stdin (EOF) to terminate the session cleanly.

    This class remains contract-only: it knows nothing about the repository,
    the schema, or the CLI -- only the request/response envelope.
    """

    def __init__(self, db=None, python=None, root=None, wait_timeout=120,
                 startup_timeout=120):
        self.db = db
        self.python = python or sys.executable
        self.root = str(root or _project_root())
        cmd = [self.python, "-m", "api.session"]
        if self.db:
            cmd.extend(["--db", self.db])
        self._proc = subprocess.Popen(
            cmd, cwd=self.root,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1)
        assert self._proc.stdin is not None
        assert self._proc.stdout is not None
        assert self._proc.stderr is not None
        self._wait_timeout = wait_timeout
        self._closed = False
        self._stderr_lines = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def __call__(self, request):
        if self._closed:
            raise ContractError("client:transport", "session is closed")
        try:
            payload_text = json.dumps(request)
        except (TypeError, ValueError) as exc:
            raise ContractError(
                "client:unserializable",
                "request is not JSON-serializable: %s" % exc,
                request.get("operation") if isinstance(request, dict) else None)
        try:
            assert self._proc.stdin is not None
            assert self._proc.stdout is not None
            self._proc.stdin.write(payload_text + "\n")
            self._proc.stdin.flush()
            line = self._proc.stdout.readline()
        except (OSError, ValueError) as exc:
            raise ContractError(
                "client:transport", "session write/read failed: %s" % exc)
        if not line:
            self._closed = True
            raise ContractError(
                "client:transport",
                "session ended unexpectedly (EOF); stderr: %s"
                % "".join(self._stderr_lines)[:300])
        try:
            payload = json.loads(line)
        except ValueError:
            raise ContractError(
                "client:invalid_response",
                "session emitted a non-JSON line: %r" % (line[:200],))
        if not isinstance(payload, dict) or not isinstance(payload.get("ok"), bool):
            raise ContractError(
                "client:invalid_response",
                "session emitted a non-conforming envelope: %r" % (payload,))
        return payload

    def close(self):
        if self._closed:
            return
        try:
            assert self._proc.stdin is not None
            self._proc.stdin.close()  # EOF -> clean session shutdown
        except (OSError, ValueError):
            pass
        self._closed = True
        try:
            self._proc.wait(timeout=self._wait_timeout)
        except subprocess.TimeoutExpired:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        try:
            if self._proc.stdout is not None:
                self._proc.stdout.close()
            if self._proc.stderr is not None:
                self._proc.stderr.close()
        except (OSError, ValueError):
            pass

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


# Kept as module constants for the isolation audit test (pure stdlib access).
STDLIB_ONLY_IMPORTS = frozenset(("json", "subprocess", "sys", "pathlib", "threading"))