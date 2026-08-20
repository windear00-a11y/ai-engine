"""Persistent Knowledge Engine session.

A long-lived local process that loads the knowledge repository ONE time and
then serves many tool requests without reloading it. This removes the ~20-30s
per-call load cost of the one-shot runner (``python -m api.tools``).

Architecture::

    External Client (stdout/stderr)
              |
              v
    SessionServer (api.session)   <-- loads DB once, keeps KnowledgeAPI alive
              |
              v
    ToolInterface (api.tools)     <-- unchanged public request/response contract
              |
              v
    KnowledgeAPI / KnowledgeRepository / SQLite

Process protocol (v1)
---------------------
* stdin:  one JSON request per line (newline delimited)
* stdout: one JSON response per line (exactly one JSON document per line)
* stderr: diagnostics only (a single ``SESSION stats=... end`` line on exit)
* EOF terminates the session cleanly.
* A malformed or invalid request yields a structured ``ok: false`` response
  and the session continues to the next line.

The existing one-shot runner (``python -m api.tools``) is intentionally kept.

The module never exposes SQLite, never executes arbitrary commands, and is
strictly read-only with respect to the knowledge database.
"""

import argparse
import json
import os
import sys
import time

from api.tools import ToolInterface
from retrieval.repository import DEFAULT_KNOWLEDGE_DB

MAX_LINE_BYTES = 1 << 20  # 1 MiB per request line (defensive cap)


def _single_env(code, message, operation=None):
    """Build a contract-shaped error envelope without touching the engine."""
    return {
        "ok": False,
        "operation": operation,
        "error": {"code": code, "message": message},
    }


class SessionServer:
    """Process one request at a time against one lazily-loaded KnowledgeAPI."""

    def __init__(self, db_path=DEFAULT_KNOWLEDGE_DB, interface_factory=None):
        self.db_path = db_path
        # interface_factory(db_path) -> object with .execute(dict) and .close().
        # Tests inject a counting factory to prove initialization happens once.
        self._factory = interface_factory or (
            lambda db: ToolInterface(db_path=db))
        self._interface = None
        self._load_failed = False
        self.stats = {
            "requests": 0,       # non-EOF lines received
            "initializations": 0,  # times the repository was loaded
            "load_seconds": None,   # wall time of the (single) repository load
        }

    # -- lifecycle ----------------------------------------------------------

    def _ensure_loaded(self):
        if self._interface is not None:
            return self._interface
        if self._load_failed:
            return None
        try:
            start = time.monotonic()
            self._interface = self._factory(self.db_path)
        except Exception:  # noqa: BLE001 - cache failure; no retry storm
            self._load_failed = True
            return None
        self.stats["load_seconds"] = time.monotonic() - start
        self.stats["initializations"] += 1
        return self._interface

    def close(self):
        if self._interface is not None:
            self._interface.close()
            self._interface = None

    # -- line processing ----------------------------------------------------

    def process_line(self, line):
        """Turn one input line into one response dict (never raises)."""
        self.stats["requests"] += 1
        text = line.rstrip("\r\n")
        if not text.strip():
            return _single_env("invalid_request", "empty request line")
        if len(text.encode("utf-8", "replace")) > MAX_LINE_BYTES:
            return _single_env("invalid_request", "request line too large")
        try:
            request = json.loads(text)
        except ValueError:
            return _single_env("invalid_request", "invalid JSON request")
        interface = self._ensure_loaded()
        if interface is None:
            return _single_env(
                "internal_error", "session could not initialize the engine")
        return interface.execute(request)

    def run(self, input_stream, output_stream, error_stream=None):
        """Serve lines until EOF, then close and report stats to stderr.

        Returns the number of requests processed.
        """
        try:
            for line in input_stream:
                if isinstance(line, bytes):
                    line = line.decode("utf-8", "replace")
                response = self.process_line(line)
                output_stream.write(json.dumps(
                    response, ensure_ascii=False, sort_keys=True) + "\n")
                output_stream.flush()
        except BrokenPipeError:  # client disconnected; stop cleanly
            pass
        finally:
            self.close()
            if error_stream is not None:
                error_stream.write("SESSION stats=%s end\n" % json.dumps(
                    self.stats, ensure_ascii=False, sort_keys=True))
                error_stream.flush()
        return self.stats["requests"]


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m api.session",
        description=(
            "Persistent Knowledge Engine session. Reads one JSON request per "
            "line on stdin and writes one JSON response per line on stdout. "
            "EOF terminates the session. Diagnostics go to stderr only."))
    parser.add_argument(
        "--db", default=DEFAULT_KNOWLEDGE_DB,
        help="database file (default: database/knowledge.db)")
    args = parser.parse_args(argv)

    if not os.path.isfile(args.db):
        sys.stderr.write(
            "SESSION startup failed: database file not found: %s\n"
            % args.db)
        sys.stderr.flush()
        return 1

    server = SessionServer(db_path=args.db)
    try:
        server.run(sys.stdin, sys.stdout, sys.stderr)
    except KeyboardInterrupt:
        server.close()
        return 130
    except Exception as exc:  # noqa: BLE001 - defensive; keep protocol clean
        sys.stderr.write("SESSION fatal error: %s\n" % (exc,))
        sys.stderr.flush()
        server.close()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())