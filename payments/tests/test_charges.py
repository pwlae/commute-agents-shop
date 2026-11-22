from prometheus_client import REGISTRY

from conftest import Stub, charge, provider_rules
import shop_http
from fastapi.testclient import TestClient

from app.main import create_app


def metric(name, **labels):
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_approved(api, provider):
    r = charge(api, token="tok_ok", headers={"x-trace-id": "t-123"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"
    assert r.json()["charge_id"].startswith("ch_")
    assert r.headers["x-trace-id"] == "t-123"
    assert provider.seen[0]["headers"]["x-trace-id"] == "t-123"


def test_same_idempotency_key_twice_is_one_charge(api, provider):
    first = charge(api, order_id="o-2", key="order-o-2")
    again = charge(api, order_id="o-2", key="order-o-2")
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()
    assert len(provider.seen) == 1
    assert len(api.get("/charges", params={"order_id": "o-2"}).json()) == 1


def test_idempotency_key_from_header(api, provider):
    body = {"order_id": "o-h", "amount_cents": 500, "card_token": "tok_ok"}
    a = api.post("/charges", json=body, headers={"idempotency-key": "k-h"})
    b = api.post("/charges", json=body, headers={"idempotency-key": "k-h"})
    assert a.json()["charge_id"] == b.json()["charge_id"]
    assert len(provider.seen) == 1


def test_missing_idempotency_key(api):
    r = api.post("/charges", json={"order_id": "o", "amount_cents": 1,
                                   "card_token": "tok_ok"})
    assert r.status_code == 422


def test_same_key_other_request_is_conflict(api):
    charge(api, order_id="o-3", amount=100, key="k-3")
    r = charge(api, order_id="o-3", amount=200, key="k-3")
    assert r.status_code == 409


def test_decline(api):
    before = metric("payments_charges_total", status="declined")
    r = charge(api, order_id="o-4", token="tok_decline")
    assert r.status_code == 200
    assert r.json()["status"] == "declined"
    assert r.json()["decline_reason"] == "card_declined"
    assert metric("payments_charges_total", status="declined") == before + 1


def test_provider_503_is_retried_by_client(store):
    calls = []

    def flaky(method, path, body):
        calls.append(path)
        if len(calls) == 1:
            return 503, {"error": "busy"}
        return provider_rules(method, path, body)

    s = Stub(flaky)
    try:
        c = shop_http.client(s.url, timeout_ms=1000, backoff_ms=10)
        with TestClient(create_app(store, c, reconcile_every_s=0)) as api:
            r = charge(api, order_id="o-5")
        assert r.json()["status"] == "approved"
        assert calls == ["/authorize", "/authorize"]
    finally:
        s.close()


def test_provider_down_is_502_and_no_charge(store):
    s = Stub(lambda *a: (503, {"error": "down"}))
    try:
        c = shop_http.client(s.url, timeout_ms=1000, backoff_ms=10)
        before = metric("card_provider_errors_total", reason="http_503")
        with TestClient(create_app(store, c, reconcile_every_s=0)) as api:
            r = charge(api, order_id="o-6")
            assert r.status_code == 502
            assert api.get("/charges",
                           params={"order_id": "o-6"}).json() == []
        assert len(s.seen) == 3
        assert metric("card_provider_errors_total",
                      reason="http_503") == before + 1
    finally:
        s.close()


def test_second_approved_charge_for_order_is_counted(api):
    before = metric("payments_duplicate_charges_total")
    charge(api, order_id="o-7", key="order-o-7-1")
    charge(api, order_id="o-7", key="order-o-7-1")
    assert metric("payments_duplicate_charges_total") == before
    charge(api, order_id="o-7", key="order-o-7-2")
    assert metric("payments_duplicate_charges_total") == before + 1


def test_refund(api):
    ch = charge(api, order_id="o-8").json()
    a = api.post("/refunds", json={"charge_id": ch["charge_id"]})
    b = api.post("/refunds", json={"charge_id": ch["charge_id"]})
    assert a.status_code == 200
    assert a.json()["status"] == "refunded"
    assert a.json()["refund_id"] == b.json()["refund_id"]


def test_refund_declined_or_missing(api):
    ch = charge(api, order_id="o-9", token="tok_decline").json()
    assert api.post("/refunds", json={
        "charge_id": ch["charge_id"]}).status_code == 409
    assert api.post("/refunds", json={
        "charge_id": "ch_nope"}).status_code == 404


def test_metrics_and_health(api):
    charge(api, order_id="o-10")
    assert api.get("/healthz").json() == {"status": "ok"}
    text = api.get("/metrics").text
    for name in ("http_requests_total", "http_request_duration_seconds",
                 "payments_charges_total",
                 "payments_duplicate_charges_total",
                 "payments_captured_for_failed_orders",
                 "card_provider_errors_total"):
        assert name in text
    assert ('http_requests_total{code="200",route="/charges",'
            'service="payments"}') in text
