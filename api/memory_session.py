"""Persistent Memory session (v2).

Like api/session.py for v1, but for Memory v2 (remember/recall etc.).
Loads MemoryToolInterface once, serves many requests.
"""

import argparse
import json
import os
import sys
import time

from api.memory_tools import MemoryToolInterface

MAX_LINE_BYTES = 1 << 20


def _single_env(code, message, operation=None):
    from api.contract_v2 import CONTRACT_VERSION
    return {
        "ok": False,
        "operation": operation,
        "contract_version": CONTRACT_VERSION,
        "error": {"code": code, "message": message},
    }


class MemorySessionServer:
    def __init__(self, data_root=None, interface_factory=None):
        self.data_root = data_root
        self._factory = interface_factory or (lambda dr: MemoryToolInterface(data_root=dr))
        self._interface = None
        self._load_failed = False
        self.stats = {"requests": 0, "initializations": 0, "load_seconds": None}

    def _ensure_loaded(self):
        if self._interface is not None:
            return self._interface
        if self._load_failed:
            return None
        try:
            start = time.monotonic()
            self._interface = self._factory(self.data_root)
        except Exception:
            self._load_failed = True
            return None
        self.stats["load_seconds"] = time.monotonic() - start
        self.stats["initializations"] += 1
        return self._interface

    def close(self):
        if self._interface is not None:
            try:
                self._interface.close()
            except Exception:
                pass
            self._interface = None

    def process_line(self, line):
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
        iface = self._ensure_loaded()
        if iface is None:
            return _single_env("internal_error", "session could not initialize memory engine")
        return iface.execute(request)

    def run(self, input_stream, output_stream, error_stream=None):
        try:
            for line in input_stream:
                if isinstance(line, bytes):
                    line = line.decode("utf-8", "replace")
                resp = self.process_line(line)
                output_stream.write(json.dumps(resp, ensure_ascii=False, sort_keys=True) + "\n")
                output_stream.flush()
        except BrokenPipeError:
            pass
        finally:
            self.close()
            if error_stream is not None:
                error_stream.write("SESSION stats=%s end\n" % json.dumps(self.stats, ensure_ascii=False, sort_keys=True))
                error_stream.flush()
        return self.stats["requests"]


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m api.memory_session", description="Persistent Memory session (v2)")
    parser.add_argument("--data-root", default=None, help="data root for Memory")
    args = parser.parse_args(argv)
    # data_root may be via AI_ENGINE_DATA_DIR env, but explicit --data-root overrides
    if args.data_root:
        os.environ["AI_ENGINE_DATA_DIR"] = args.data_root
    server = MemorySessionServer(data_root=args.data_root)
    try:
        server.run(sys.stdin, sys.stdout, sys.stderr)
    except KeyboardInterrupt:
        server.close()
        return 130
    except Exception as exc:
        sys.stderr.write(f"SESSION fatal error: {exc}\n")
        sys.stderr.flush()
        server.close()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
