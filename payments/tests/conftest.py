import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import shop_http
from fastapi.testclient import TestClient

from app.main import create_app
from app.store import MemoryStore


class Stub:
    """A local HTTP server. `handle(method, path, body)` returns
    (status, json_body). Every request is recorded in `seen`."""

    def __init__(self, handle):
        self.handle = handle
        self.seen = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _reply(self):
                n = int(self.headers.get("content-length") or 0)
                raw = self.rfile.read(n)
                body = json.loads(raw) if raw else None
                stub.seen.append({"method": self.command,
                                  "path": self.path, "body": body,
                                  "headers": {k.lower(): v for k, v
                                              in self.headers.items()}})
                status, out = stub.handle(self.command, self.path, body)
                data = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

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


def provider_rules(method, path, body):
    """Same token rules as card-provider/app.py."""
    approved = body["token"] == "tok_ok"
    return 200, {"authorization_id": "auth_test", "approved": approved,
                 "reason": None if approved else "card_declined"}


@pytest.fixture
def provider():
    s = Stub(provider_rules)
    yield s
    s.close()


@pytest.fixture
def store():
    dsn = os.environ.get("PAYMENTS_TEST_DSN")
    if not dsn:
        yield MemoryStore()
        return
    import psycopg
    from psycopg_pool import ConnectionPool

    from app import migrate
    from app.store import PgStore
    with psycopg.connect(dsn, autocommit=True) as c:
        migrate.up(c)
        c.execute("TRUNCATE refunds, charges")
    pool = ConnectionPool(dsn, min_size=1, max_size=4, open=True)
    yield PgStore(pool)
    pool.close()


@pytest.fixture
def api(store, provider):
    c = shop_http.client(provider.url, timeout_ms=1000, backoff_ms=10)
    with TestClient(create_app(store, c, reconcile_every_s=0)) as tc:
        yield tc


def charge(api, order_id="o-1", amount=1999, key=None, token="tok_ok",
           **kw):
    return api.post("/charges", json={
        "order_id": order_id, "amount_cents": amount,
        "idempotency_key": key or f"order-{order_id}",
        "card_token": token}, **kw)
