"""Steady fake shoppers for the practice shop. Standard library only.

Places orders through the gateway and reads each one back a few
seconds later. The mix is mostly paid and declined single-item orders,
some out-of-stock ones and a few bad requests, so every dashboard
panel has something on it.

Stock is never refilled: at the default rate the confirmed orders use
up the seed stock in about 20 minutes, after which most orders end
REJECTED. `docker compose down -v` starts again from 50 of each SKU.
"""

import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from collections import deque

GATEWAY = os.environ.get("GATEWAY_URL", "http://localhost:8080")
API_KEY = os.environ.get("API_KEY", "dev-key")
RATE = float(os.environ.get("ORDERS_PER_S", "0.5"))
SKUS = ["MAT-01", "LAMP-02", "CHAIR-03", "CABLE-04", "MUG-05"]


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        GATEWAY + path, data, method=method,
        headers={"x-api-key": API_KEY,
                 "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return 0, None


def next_order(rnd):
    roll = rnd.random()
    sku = rnd.choice(SKUS)
    customer = f"c-{rnd.randint(1, 40)}"
    if roll < 0.45:
        items, token = [{"sku": sku, "qty": 1}], "tok_ok"
    elif roll < 0.85:
        items, token = [{"sku": sku, "qty": rnd.randint(1, 2)}], \
            "tok_decline"
    elif roll < 0.95:
        items, token = [{"sku": sku, "qty": 999}], "tok_ok"
    else:
        items, token = [{"sku": "NOPE-99", "qty": 1}], "tok_ok"
    return {"customer_id": customer, "items": items,
            "card_token": token}


def main():
    rnd = random.Random(os.environ.get("SEED"))
    pending = deque()
    gap = 1.0 / RATE if RATE > 0 else 1.0
    print(f"traffic: {RATE} orders/s to {GATEWAY}", flush=True)
    while True:
        started = time.monotonic()
        code, body = call("POST", "/orders", next_order(rnd))
        if code == 202 and body:
            pending.append((started, body["order_id"]))
        print(f"traffic: POST /orders {code}", flush=True)
        while pending and started - pending[0][0] > 3:
            _, oid = pending.popleft()
            code, body = call("GET", f"/orders/{oid}")
            status = body.get("status") if body else "-"
            print(f"traffic: GET /orders/{oid} {code} {status}",
                  flush=True)
        time.sleep(max(0.0, gap - (time.monotonic() - started)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
