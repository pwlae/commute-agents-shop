import logging
import threading
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import shop_http
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from . import reconciler
from .metrics import (CHARGES, DUPLICATE_CHARGES, HTTP_DURATION,
                      HTTP_REQUESTS, PROVIDER_ERRORS, SERVICE)

log = logging.getLogger("payments.api")

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
QUIET = {"/metrics", "/healthz"}


class ChargeIn(BaseModel):
    order_id: str = Field(min_length=1)
    amount_cents: int = Field(gt=0)
    card_token: str = Field(min_length=1)
    idempotency_key: Optional[str] = None


class RefundIn(BaseModel):
    charge_id: str = Field(min_length=1)


def charge_out(ch):
    return {"charge_id": ch["id"], "status": ch["status"],
            "order_id": ch["order_id"],
            "amount_cents": ch["amount_cents"],
            "decline_reason": ch["decline_reason"]}


def create_app(store, provider, orders=None, reconcile_every_s=15):
    stop = threading.Event()

    @asynccontextmanager
    async def lifespan(app):
        if orders is not None and reconcile_every_s:
            reconciler.start(store, orders, reconcile_every_s, stop)
        yield
        stop.set()

    app = FastAPI(title="payments", lifespan=lifespan)

    @app.middleware("http")
    async def observe(request: Request, call_next):
        trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())
        request.state.trace_id = trace_id
        start = time.perf_counter()
        code = 500
        try:
            response = await call_next(request)
            code = response.status_code
            response.headers["x-trace-id"] = trace_id
            return response
        finally:
            took = time.perf_counter() - start
            route = request.scope.get("route")
            path = route.path if route else "unmatched"
            HTTP_REQUESTS.labels(SERVICE, path, str(code)).inc()
            HTTP_DURATION.labels(SERVICE, path).observe(took)
            if request.url.path not in QUIET:
                log.info("%s %s %s %.0fms trace_id=%s",
                         request.method, request.url.path, code,
                         took * 1000, trace_id)

    @app.get("/healthz")
    def healthz():
        store.ping()
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    @app.post("/charges")
    def create_charge(body: ChargeIn, request: Request,
                      idempotency_key: Optional[str] = Header(None)):
        trace_id = request.state.trace_id
        key = body.idempotency_key or idempotency_key
        if not key:
            raise HTTPException(422, "idempotency_key is required")
        ch, created = store.create_pending(
            body.order_id, body.amount_cents, key)
        if not created:
            if (ch["order_id"] != body.order_id
                    or ch["amount_cents"] != body.amount_cents):
                raise HTTPException(
                    409, "idempotency_key was used for another charge")
            if ch["status"] == "pending":
                raise HTTPException(409, "charge is still in progress")
            log.info("repeat charge_id=%s idempotency_key=%s trace_id=%s",
                     ch["id"], key, trace_id)
            return charge_out(ch)

        try:
            r = provider.post(
                "/authorize",
                {"token": body.card_token,
                 "amount_cents": body.amount_cents,
                 "reference": ch["id"]},
                idempotency_key=ch["id"], trace_id=trace_id)
        except (shop_http.TimeoutError, shop_http.ConnectError,
                shop_http.CircuitOpenError) as e:
            store.discard(ch["id"])
            reason = type(e).__name__.replace("Error", "").lower()
            PROVIDER_ERRORS.labels(reason).inc()
            log.error("card provider %s order_id=%s trace_id=%s: %s",
                      reason, body.order_id, trace_id, e)
            raise HTTPException(502, "card provider unavailable")
        if r.status != 200:
            store.discard(ch["id"])
            PROVIDER_ERRORS.labels(f"http_{r.status}").inc()
            log.error("card provider returned %s order_id=%s trace_id=%s",
                      r.status, body.order_id, trace_id)
            raise HTTPException(502, "card provider error")

        result = r.json()
        status = "approved" if result.get("approved") else "declined"
        if status == "approved" and any(
                c["status"] == "approved" and c["id"] != ch["id"]
                for c in store.by_order(body.order_id)):
            DUPLICATE_CHARGES.inc()
            log.warning("second approved charge for order_id=%s"
                        " charge_id=%s trace_id=%s",
                        body.order_id, ch["id"], trace_id)
        ch = store.finish(ch["id"], status, result.get("authorization_id"),
                          result.get("reason"))
        CHARGES.labels(status).inc()
        log.info("charge %s charge_id=%s order_id=%s amount_cents=%s"
                 " trace_id=%s", status, ch["id"], body.order_id,
                 body.amount_cents, trace_id)
        return charge_out(ch)

    @app.get("/charges/{charge_id}")
    def get_charge(charge_id: str):
        ch = store.get(charge_id)
        if not ch:
            raise HTTPException(404, "charge not found")
        return charge_out(ch)

    @app.get("/charges")
    def list_charges(order_id: str):
        return [charge_out(c) for c in store.by_order(order_id)]

    @app.post("/refunds")
    def create_refund(body: RefundIn, request: Request):
        ch = store.get(body.charge_id)
        if not ch:
            raise HTTPException(404, "charge not found")
        if ch["status"] != "approved":
            raise HTTPException(409, f"charge is {ch['status']}")
        refund, created = store.create_refund(ch["id"])
        if created:
            log.info("refund refund_id=%s charge_id=%s trace_id=%s",
                     refund["id"], ch["id"], request.state.trace_id)
        return {"refund_id": refund["id"], "charge_id": ch["id"],
                "amount_cents": ch["amount_cents"], "status": "refunded"}

    return app
