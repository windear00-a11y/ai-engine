"""Command-line entry point for the HTTP transport::

    python -m http_server [--host HOST] [--port PORT] [--db PATH] [--api-key KEY]

The server binds, prints a single ``HTTP listening on ...`` line to stderr,
and serves until interrupted (SIGINT/SIGTERM). The repository is loaded
lazily on the first ``POST /v1/execute`` request.
"""

import argparse
import sys

from http_server.server import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAX_BODY_BYTES,
    KnowledgeHTTPServer,
)


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="http_server",
        description="Expose the frozen Knowledge Engine Public Contract v1 over HTTP.")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--db", default=None,
                        help="path to the knowledge database")
    parser.add_argument("--api-key", default=None,
                        help="require this X-API-Key (and Bearer) on every call")
    parser.add_argument("--max-body-bytes", type=int, default=MAX_BODY_BYTES)
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if args.port < 0 or args.port > 65535:
        raise SystemExit("error: --port must be between 0 and 65535")

    if not args.host:
        args.host = DEFAULT_HOST
    server = KnowledgeHTTPServer(
        (args.host, args.port),
        db_path=args.db,
        api_key=args.api_key,
        max_body_bytes=args.max_body_bytes,
    )
    try:
        bind_host, bind_port = server.server_address[:2]
        sys.stderr.write("HTTP listening on %s:%d\n" % (bind_host, bind_port))
        sys.stderr.flush()
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.close()


if __name__ == "__main__":
    main()