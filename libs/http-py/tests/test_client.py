import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import shop_http


class Stub:
    """Local stub server: replies from a script, records requests."""

    def __init__(self):
        self.replies = [(200, 0)]
        self.seen = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self):
                n = int(self.headers.get("content-length") or 0)
                self.rfile.read(n)
                stub.seen.append(
                    {k.lower(): v for k, v in self.headers.items()})
                if len(stub.replies) > 1:
                    status, delay = stub.replies.pop(0)
                else:
                    status, delay = stub.replies[0]
                time.sleep(delay)
                body = b'{"ok": 1}'
                try:
                    self.send_response(status)
                    self.send_header("content-type", "application/json")
                    self.send_header("content-length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except OSError:
                    pass

            do_GET = do_POST = _reply

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, args=(0.05,),
                         daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub():
    s = Stub()
    yield s
    s.close()


def test_retries_503_then_succeeds(stub):
    stub.replies = [(503, 0), (503, 0), (200, 0)]
    res = shop_http.client(stub.url, backoff_ms=1).get("/x")
    assert res.status == 200
    assert res.json() == {"ok": 1}
    assert len(stub.seen) == 3


def test_returns_last_503_when_retries_run_out(stub):
    stub.replies = [(503, 0)]
    res = shop_http.client(stub.url, backoff_ms=1).get("/x")
    assert res.status == 503
    assert len(stub.seen) == 3


def test_no_retry_on_400(stub):
    stub.replies = [(400, 0)]
    res = shop_http.client(stub.url, backoff_ms=1).post("/x", {"a": 1})
    assert res.status == 400
    assert len(stub.seen) == 1


def test_no_retry_on_500(stub):
    stub.replies = [(500, 0)]
    res = shop_http.client(stub.url, backoff_ms=1).get("/x")
    assert res.status == 500
    assert len(stub.seen) == 1


def test_timeout_is_not_retried(stub):
    stub.replies = [(200, 0.3)]
    c = shop_http.client(stub.url, timeout_ms=50, backoff_ms=1)
    with pytest.raises(shop_http.TimeoutError):
        c.get("/slow")
    assert len(stub.seen) == 1


def test_connect_error_retried_then_raised():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    c = shop_http.client(f"http://127.0.0.1:{port}", backoff_ms=20)
    t0 = time.monotonic()
    with pytest.raises(shop_http.ConnectError):
        c.get("/x")
    # two retries: 20 ms + 40 ms of backoff
    assert time.monotonic() - t0 >= 0.055


def test_breaker_opens_after_5_failures_and_rejects_fast(stub):
    stub.replies = [(503, 0)]
    c = shop_http.client(stub.url, retries=0,
                         breaker={"failures": 5, "open_ms": 60000})
    for _ in range(5):
        assert c.get("/x").status == 503
    assert c.breaker_state == "open"
    t0 = time.monotonic()
    with pytest.raises(shop_http.CircuitOpenError):
        c.get("/x")
    assert time.monotonic() - t0 < 0.02
    assert len(stub.seen) == 5


def test_breaker_half_opens_and_closes_on_success(stub):
    stub.replies = [(503, 0), (200, 0)]
    c = shop_http.client(stub.url, retries=0,
                         breaker={"failures": 1, "open_ms": 30})
    c.get("/x")
    with pytest.raises(shop_http.CircuitOpenError):
        c.get("/x")
    time.sleep(0.04)
    assert c.get("/x").status == 200
    assert c.breaker_state == "closed"


def test_4xx_is_not_a_failure(stub):
    stub.replies = [(404, 0)]
    c = shop_http.client(stub.url, breaker={"failures": 2})
    for _ in range(4):
        c.get("/x")
    assert c.breaker_state == "closed"


def test_trace_id_and_idempotency_key_forwarded(stub):
    stub.replies = [(502, 0), (200, 0)]
    c = shop_http.client(stub.url, backoff_ms=1)
    res = c.post("/charges", {"a": 1}, idempotency_key="order-1",
                 trace_id="trace-abc")
    assert res.trace_id == "trace-abc"
    assert [h.get("x-trace-id") for h in stub.seen] == ["trace-abc"] * 2
    assert stub.seen[1].get("idempotency-key") == "order-1"
    assert stub.seen[1].get("content-type") == "application/json"


def test_trace_id_generated_when_absent(stub):
    res = shop_http.client(stub.url).get("/x")
    assert len(res.trace_id) == 36
    assert stub.seen[0].get("x-trace-id") == res.trace_id
