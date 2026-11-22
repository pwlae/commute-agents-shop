import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createApp } from "../src/api.js";
import { setup, silent } from "./fakes.js";

let t: ReturnType<typeof setup>;
let server: Server;
let base: string;

beforeEach(async () => {
  t = setup();
  const app = createApp({ ...t, log: silent });
  server = await new Promise<Server>((ok) => {
    const s = app.listen(0, "127.0.0.1", () => ok(s));
  });
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterEach(async () => {
  server.closeAllConnections();
  await new Promise((ok) => server.close(ok));
});

const post = (body: unknown, headers: Record<string, string> = {}) =>
  fetch(`${base}/orders`, {
    method: "POST",
    headers: { "content-type": "application/json", ...headers },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });

const good = {
  customer_id: "c-1",
  items: [{ sku: "LAMP-02", qty: 2 }, { sku: "CABLE-04", qty: 3 }],
  card_token: "tok_ok",
};

describe("api", () => {
  it("POST /orders -> 202 PENDING, keeps the trace id", async () => {
    const res = await post(good, { "x-trace-id": "abc" });
    expect(res.status).toBe(202);
    expect(res.headers.get("x-trace-id")).toBe("abc");
    const body = await res.json();
    expect(body.status).toBe("PENDING");
    expect(t.sent[0].traceId).toBe("abc");
  });

  it("POST /orders with a bad body -> 400", async () => {
    for (const b of [
      {},
      { ...good, items: [] },
      { ...good, items: [{ sku: "MAT-01", qty: 0 }] },
      { ...good, card_token: undefined },
      "{not json",
    ]) {
      expect((await post(b)).status).toBe(400);
    }
    expect(t.sent).toEqual([]);
  });

  it("POST /orders with an unknown sku -> 400", async () => {
    const res = await post({ ...good, items: [{ sku: "X-1", qty: 1 }] });
    expect(res.status).toBe(400);
    expect(await res.json()).toEqual({ error: "unknown sku", skus: ["X-1"] });
  });

  it("GET /orders/:id returns status, items and total", async () => {
    const { order_id } = await (await post(good)).json();
    const res = await fetch(`${base}/orders/${order_id}`);
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      order_id,
      status: "PENDING",
      items: [
        { sku: "LAMP-02", qty: 2, unit_price: 34.5 },
        { sku: "CABLE-04", qty: 3, unit_price: 4.25 },
      ],
      total: 81.75,
    });
  });

  it("GET unknown order -> 404", async () => {
    expect((await fetch(`${base}/orders/nope`)).status).toBe(404);
    const id = "00000000-0000-4000-8000-000000000000";
    expect((await fetch(`${base}/orders/${id}`)).status).toBe(404);
  });

  it("serves /healthz and /metrics", async () => {
    expect((await fetch(`${base}/healthz`)).status).toBe(200);
    await post(good);
    const text = await (await fetch(`${base}/metrics`)).text();
    expect(text).toContain(
      'http_requests_total{service="orders",route="/orders",code="202"} 1',
    );
    expect(text).toContain("http_request_duration_seconds_bucket");
    expect(text).toContain("orders_saga_transitions_total");
    expect(text).toContain("orders_pending_oldest_seconds 0");
  });
});
