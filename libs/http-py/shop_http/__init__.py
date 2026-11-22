"""Shared HTTP client: timeouts, retries, circuit breaker, x-trace-id.

Standard library only, so any service can use it without extra
dependencies. Calls are blocking; from async code run them in a
thread (for example ``await asyncio.to_thread(c.post, ...)``).
"""
from __future__ import annotations

import json as _json
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

__all__ = [
    "Client", "Response", "client",
    "CircuitOpenError", "ConnectError", "TimeoutError",
    "TRACE_HEADER", "IDEMPOTENCY_HEADER",
]

TRACE_HEADER = "x-trace-id"
IDEMPOTENCY_HEADER = "idempotency-key"
RETRY_STATUSES = frozenset({502, 503, 504})


class CircuitOpenError(Exception):
    pass


class ConnectError(Exception):
    pass


class TimeoutError(Exception):  # noqa: A001 - shadows builtin on purpose
    pass


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes
    trace_id: str

    def json(self) -> Any:
        return _json.loads(self.body)

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


def _is_timeout(err: BaseException) -> bool:
    if isinstance(err, (socket.timeout, TimeoutError)):
        return True
    reason = getattr(err, "reason", None)
    return isinstance(reason, socket.timeout)


def _is_connect(err: BaseException) -> bool:
    # urllib wraps errors from connecting and sending in URLError;
    # errors while reading the response are not wrapped.
    return (isinstance(err, urllib.error.URLError)
            and isinstance(err.reason, OSError))


@dataclass
class Client:
    base_url: str
    timeout_ms: int = 2000
    retries: int = 2
    backoff_ms: int = 100
    breaker: dict = field(default_factory=dict)

    def __post_init__(self):
        self.base_url = self.base_url.rstrip("/")
        self._max_failures = self.breaker.get("failures", 5)
        self._open_ms = self.breaker.get("open_ms", 10000)
        self._failures = 0
        self._opened_at: Optional[float] = None
        self._lock = threading.Lock()

    @property
    def breaker_state(self) -> str:
        if self._opened_at is None:
            return "closed"
        elapsed = (time.monotonic() - self._opened_at) * 1000
        return "open" if elapsed < self._open_ms else "half-open"

    def get(self, path: str, trace_id: Optional[str] = None,
            headers: Optional[dict] = None) -> Response:
        return self._request("GET", path, None, None, trace_id, headers)

    def post(self, path: str, json: Any = None,
             idempotency_key: Optional[str] = None,
             trace_id: Optional[str] = None,
             headers: Optional[dict] = None) -> Response:
        return self._request("POST", path, json, idempotency_key,
                             trace_id, headers)

    def _request(self, method, path, json, idempotency_key,
                 trace_id, extra_headers) -> Response:
        if self.breaker_state == "open":
            raise CircuitOpenError(f"circuit open for {self.base_url}")
        trace_id = trace_id or str(uuid.uuid4())
        headers = dict(extra_headers or {})
        headers[TRACE_HEADER] = trace_id
        data = None
        if json is not None:
            data = _json.dumps(json).encode()
            headers["content-type"] = "application/json"
        if idempotency_key:
            headers[IDEMPOTENCY_HEADER] = idempotency_key
        url = self.base_url + path

        attempt = 0
        while True:
            last = attempt >= self.retries
            req = urllib.request.Request(url, data, headers, method=method)
            try:
                with urllib.request.urlopen(
                        req, timeout=self.timeout_ms / 1000) as r:
                    status, hdrs, body = r.status, dict(r.headers), r.read()
            except urllib.error.HTTPError as e:
                status, hdrs = e.code, dict(e.headers or {})
                try:
                    body = e.read()
                except Exception:
                    body = b""
            except Exception as e:
                if _is_timeout(e):
                    # Not retried: the server may still be doing the work.
                    self._on_failure()
                    raise TimeoutError(
                        f"timeout after {self.timeout_ms} ms: {url}") from e
                if _is_connect(e):
                    if not last:
                        self._sleep(attempt)
                        attempt += 1
                        continue
                    self._on_failure()
                    raise ConnectError(f"connect failed: {url}") from e
                self._on_failure()
                raise
            if status in RETRY_STATUSES and not last:
                self._sleep(attempt)
                attempt += 1
                continue
            if status >= 500:
                self._on_failure()
            else:
                self._on_success()
            return Response(status, hdrs, body, trace_id)

    def _sleep(self, attempt: int) -> None:
        time.sleep(self.backoff_ms * (2 ** attempt) / 1000)

    def _on_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def _on_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if (self.breaker_state == "half-open"
                    or self._failures >= self._max_failures):
                self._opened_at = time.monotonic()


def client(base_url: str, timeout_ms: int = 2000, retries: int = 2,
           backoff_ms: int = 100, breaker: Optional[dict] = None) -> Client:
    return Client(base_url, timeout_ms, retries, backoff_ms,
                  dict(breaker or {}))
