"""HTTP transport for the external client.

Delivers requests to the Knowledge Engine through the public HTTP contract:
one ``POST /v1/execute`` per request, one JSON envelope back.  The transport
knows *only* the wire format (JSON over HTTP) and never touches SQLite,
the repository, or any internal module.

Design decisions
----------------
* Uses stdlib ``http.client.HTTPConnection`` only -- no ``requests``/``urllib3``.
* Single persistent connection with lazy creation; reconnected automatically
  after any non-200 response (to avoid stale request body bytes on the
  server-side keep-alive socket).
* ``execute_raw`` exposes low-level POST for transport-level error tests
  (malformed JSON, wrong content-type, oversized body).
"""

import http.client
import json
import socket
import time
from typing import Any, Dict, Optional, Tuple, Union

from external_http_client.errors import (
    InvalidResponseError,
    TransportError,
)

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "DEFAULT_TIMEOUT",
    "HTTPTransport",
]

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_TIMEOUT = 30.0
_CONTRACT_PATH = "/v1/execute"
_HEALTH_PATH = "/health"


class HTTPTransport:
    """Deliver Contract v1 requests over HTTP.

    The transport owns one ``http.client.HTTPConnection`` that is lazily
    created and reused for consecutive requests.  After any non-200 response
    the connection is closed and re-established on the next call, preventing
    stale request-body bytes from corrupting the keep-alive stream.

    Parameters
    ----------
    host, port : server address.
    api_key : optional; sent as ``X-API-Key`` header on every request.
    timeout : socket timeout in seconds (default 30).
    """

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT,
                 api_key=None, timeout=DEFAULT_TIMEOUT):
        self.host = host
        self.port = port
        self.api_key = api_key
        self.timeout = timeout
        self.stats = {"requests": 0, "http_errors": 0, "last_status": None}
        self._conn = None

    # -- connection management ----------------------------------------------

    def _connect(self):
        if self._conn is None:
            self._conn = http.client.HTTPConnection(
                self.host, self.port, timeout=self.timeout)
        return self._conn

    def _close(self):
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    # -- low-level POST -----------------------------------------------------

    def execute_raw(self, body, content_type="application/json",
                    content_length=None):
        """POST to ``/v1/execute``; return ``(status, text)``.

        The connection is closed after any non-200 response to avoid
        leftover request-body bytes poisoning the keep-alive socket.

        Parameters
        ----------
        body : bytes to send as the request body.
        content_type : Content-Type header (default ``application/json``).
        content_length : explicit Content-Length; defaults to ``len(body)``.
        """
        conn = self._connect()
        hdrs = {}
        hdrs["Content-Type"] = content_type
        hdrs["Content-Length"] = (
            str(content_length) if content_length is not None else str(len(body)))
        if self.api_key:
            hdrs["X-API-Key"] = self.api_key
        try:
            conn.request("POST", _CONTRACT_PATH, body=body, headers=hdrs)
            resp = conn.getresponse()
            text = resp.read().decode("utf-8")
            status = resp.status
        except (OSError, socket.timeout, http.client.HTTPException,
                ValueError) as exc:
            self._close()
            raise TransportError(
                "HTTP request failed: %s" % (exc,)) from exc
        self.stats["requests"] += 1
        self.stats["last_status"] = status
        if status != 200:
            self.stats["http_errors"] += 1
            self._close()
        return status, text

    # -- contract-level execute ---------------------------------------------

    def execute(self, request):
        """POST a request dict; return the parsed response envelope dict.

        Raises :class:`~external_http_client.errors.TransportError` on I/O
        failure and :class:`~external_http_client.errors.InvalidResponseError`
        when the server response is not a conforming JSON envelope.
        """
        try:
            body = json.dumps(request).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise TransportError(
                "request is not JSON-serializable: %s" % (exc,)) from exc
        status, text = self.execute_raw(body)
        try:
            envelope = json.loads(text)
        except ValueError:
            raise InvalidResponseError(
                "server returned non-JSON on HTTP %d: %r" % (
                    status, text[:200]),
                http_status=status)
        if not isinstance(envelope, dict):
            raise InvalidResponseError(
                "server returned a non-object response on HTTP %d" % status,
                http_status=status)
        if not isinstance(envelope.get("ok"), bool):
            raise InvalidResponseError(
                "server returned a non-conforming envelope on HTTP %d" % status,
                http_status=status)
        envelope["_http_status"] = status
        return envelope

    # -- GET /health --------------------------------------------------------

    def health(self):
        """GET /health; return the parsed health envelope dict."""
        conn = http.client.HTTPConnection(
            self.host, self.port, timeout=self.timeout)
        try:
            conn.request("GET", _HEALTH_PATH)
            resp = conn.getresponse()
            text = resp.read().decode("utf-8")
            status = resp.status
        except (OSError, socket.timeout, http.client.HTTPException,
                ValueError) as exc:
            raise TransportError(
                "health check failed: %s" % (exc,)) from exc
        finally:
            conn.close()
        try:
            envelope = json.loads(text)
        except ValueError:
            raise InvalidResponseError(
                "health returned non-JSON on HTTP %d" % status,
                http_status=status)
        if not isinstance(envelope, dict):
            raise InvalidResponseError(
                "health returned non-object on HTTP %d" % status,
                http_status=status)
        return envelope

    # -- lifecycle ----------------------------------------------------------

    def close(self):
        """Close the underlying HTTP connection."""
        self._close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False
