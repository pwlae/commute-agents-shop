from prometheus_client import REGISTRY

import shop_http
from conftest import Stub, charge
from app import reconciler


def gauge():
    return REGISTRY.get_sample_value("payments_captured_for_failed_orders")


def test_counts_approved_charges_for_failed_orders(api, store):
    charge(api, order_id="ok-1")
    charge(api, order_id="bad-1")
    charge(api, order_id="bad-2", token="tok_decline")
    refunded = charge(api, order_id="bad-3").json()
    api.post("/refunds", json={"charge_id": refunded["charge_id"]})

    status = {"ok-1": "CONFIRMED", "bad-1": "CANCELLED",
              "bad-2": "CANCELLED", "bad-3": "CANCELLED"}
    orders = Stub(lambda m, path, b: (
        200, {"order_id": path.rsplit("/", 1)[1],
              "status": status[path.rsplit("/", 1)[1]]}))
    try:
        c = shop_http.client(orders.url, timeout_ms=1000)
        assert reconciler.reconcile_once(store, c) == 1
        assert gauge() == 1
        paths = sorted(s["path"] for s in orders.seen)
        assert paths == ["/orders/bad-1", "/orders/ok-1"]
    finally:
        orders.close()


def test_orders_down_keeps_last_value(api, store):
    charge(api, order_id="x-1")
    reconciler.CAPTURED_FOR_FAILED.set(4)
    c = shop_http.client("http://127.0.0.1:9", timeout_ms=200,
                         retries=0)
    assert reconciler.reconcile_once(store, c) is None
    assert gauge() == 4
