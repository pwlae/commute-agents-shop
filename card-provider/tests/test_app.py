import time

import pytest
from fastapi.testclient import TestClient

import app as provider


@pytest.fixture
def api():
    provider.chaos = provider.Chaos()
    with TestClient(provider.app) as c:
        yield c
    provider.chaos = provider.Chaos()


def auth(api, token):
    return api.post("/authorize", json={
        "token": token, "amount_cents": 1000, "reference": "ch_1"})


def test_tokens(api):
    assert auth(api, "tok_ok").json()["approved"] is True
    d = auth(api, "tok_decline").json()
    assert d["approved"] is False and d["reason"] == "card_declined"
    assert auth(api, "tok_other").json()["reason"] == "unknown_token"


def test_chaos_latency(api):
    api.post("/admin/chaos", json={"latency_ms": 300})
    start = time.perf_counter()
    assert auth(api, "tok_ok").status_code == 200
    assert time.perf_counter() - start >= 0.3


def test_chaos_decline_rate(api):
    api.post("/admin/chaos", json={"latency_ms": 0, "decline_rate": 1.0})
    assert auth(api, "tok_ok").json()["reason"] == "issuer_declined"
    assert api.get("/admin/chaos").json()["decline_rate"] == 1.0


def test_chaos_error_rate(api):
    api.post("/admin/chaos", json={"error_rate": 1.0})
    assert auth(api, "tok_ok").status_code == 503


def test_chaos_validation(api):
    r = api.post("/admin/chaos", json={"decline_rate": 2})
    assert r.status_code == 422


def test_metrics(api):
    auth(api, "tok_ok")
    text = api.get("/metrics").text
    assert ('http_requests_total{code="200",route="/authorize",'
            'service="card-provider"}') in text
