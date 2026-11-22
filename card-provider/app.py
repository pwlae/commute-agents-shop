"""Fake card provider. Not a real payment network.

Tokens: tok_ok approves, tok_decline declines, anything else is
declined as unknown. POST /admin/chaos makes it slow or flaky.
"""
import asyncio
import logging
import random
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import (CONTENT_TYPE_LATEST, Counter, Histogram,
                               disable_created_metrics, generate_latest)
from pydantic import BaseModel, Field

SERVICE = "card-provider"
disable_created_metrics()
log = logging.getLogger("card_provider")

HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests served",
                        ["service", "route", "code"])
HTTP_DURATION = Histogram(
    "http_request_duration_seconds", "HTTP request duration",
    ["service", "route"],
    buckets=(.005, .01, .025, .05, .1, .25, .5, 1, 2, 5, 10))
AUTHORIZATIONS = Counter("card_provider_authorizations",
                         "Authorizations by result", ["result"])


class Chaos(BaseModel):
    latency_ms: int = Field(0, ge=0, le=60000)
    decline_rate: float = Field(0.0, ge=0.0, le=1.0)
    error_rate: float = Field(0.0, ge=0.0, le=1.0)


class AuthorizeIn(BaseModel):
    token: str
    amount_cents: int = Field(gt=0)
    reference: str = ""


chaos = Chaos()
app = FastAPI(title="card-provider")


@app.middleware("http")
async def observe(request: Request, call_next):
    start = time.perf_counter()
    code = 500
    try:
        response = await call_next(request)
        code = response.status_code
        return response
    finally:
        route = request.scope.get("route")
        path = route.path if route else "unmatched"
        HTTP_REQUESTS.labels(SERVICE, path, str(code)).inc()
        HTTP_DURATION.labels(SERVICE, path).observe(
            time.perf_counter() - start)


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get("/admin/chaos")
def get_chaos():
    return chaos.model_dump()


@app.post("/admin/chaos")
def set_chaos(body: Chaos):
    global chaos
    chaos = body
    log.warning("chaos set: %s", chaos.model_dump())
    return chaos.model_dump()


@app.post("/authorize")
async def authorize(body: AuthorizeIn, request: Request):
    trace_id = request.headers.get("x-trace-id", "")
    if chaos.latency_ms:
        await asyncio.sleep(chaos.latency_ms / 1000)
    if chaos.error_rate and random.random() < chaos.error_rate:
        AUTHORIZATIONS.labels("error").inc()
        return JSONResponse({"error": "temporarily unavailable"}, 503)
    if body.token == "tok_ok":
        approved = random.random() >= chaos.decline_rate
        reason = None if approved else "issuer_declined"
    elif body.token == "tok_decline":
        approved, reason = False, "card_declined"
    else:
        approved, reason = False, "unknown_token"
    result = "approved" if approved else "declined"
    AUTHORIZATIONS.labels(result).inc()
    log.info("authorize %s reference=%s amount_cents=%s trace_id=%s",
             result, body.reference, body.amount_cents, trace_id)
    return {"authorization_id": "auth_" + uuid.uuid4().hex[:20],
            "approved": approved, "reason": reason}


if __name__ == "__main__":
    import uvicorn
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run(app, host="0.0.0.0", port=8100,
                log_config=None, access_log=False)
