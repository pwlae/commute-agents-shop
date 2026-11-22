import { createServer, IncomingMessage, Server } from "node:http";
import { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { createApp } from "../src/app.js";

// A fake orders service that records what it received.
type Seen = { req: IncomingMessage; body: string };

let orders: Server;
let gateway: Server;
let base: string;
let seen: Seen[];
let logs: any[];
let reply: { status: number; body: unknown };

const listen = (s: Server) =>
  new Promise<string>((ok) =>
    s.listen(0, "127.0.0.1", () =>
      ok(`http://127.0.0.1:${(s.address() as AddressInfo).port}`)));

const close = (s: Server) => {
  s.closeAllConnections();
  return new Promise((ok) => s.close(ok));
};

async function start(ordersUrl: string) {
  const app = createApp({
    ordersUrl,
    apiKeys: ["dev-key"],
    log: (level, msg, f) => logs.push({ level, msg, ...f }),
  });
  gateway = createServer(app);
  base = await listen(gateway);
}

const good = {
  customer_id: "c-1",
  items: [{ sku: "MAT-01", qty: 2 }],
  card_token: "tok_ok",
};

const post = (body: unknown, headers: Record<string, string> = {}) =>
  fetch(`${base}/orders`, {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": "dev-key",
      ...headers,
    },
    body: typeof body === "string" ? body : JSON.stringify(body),
  });

beforeEach(async () => {
  seen = [];
  logs = [];
  reply = { status: 202, body: { order_id: "o-1", status: "PENDING" } };
  orders = createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      seen.push({ req, body });
      res.writeHead(reply.status, { "content-type": "application/json" });
      res.end(JSON.stringify(reply.body));
    });
  });
  await start(await listen(orders));
});

afterEach(async () => {
  await close(gateway);
  if (orders.listening) await close(orders);
});

describe("gateway", () => {
  it("401 without an API key, orders not called", async () => {
    const res = await post(good, { "x-api-key": "" });
    expect(res.status).toBe(401);
    expect(seen.length).toBe(0);
  });

  it("401 with a wrong API key, even for a bad body", async () => {
    const res = await post("{nope", { "x-api-key": "other" });
    expect(res.status).toBe(401);
  });

  it.each([
    ["not JSON", "{nope"],
    ["array", [good]],
    ["no customer", { ...good, customer_id: "" }],
    ["no items", { ...good, items: [] }],
    ["qty 0", { ...good, items: [{ sku: "MAT-01", qty: 0 }] }],
    ["qty 1.5", { ...good, items: [{ sku: "MAT-01", qty: 1.5 }] }],
    ["qty as text", { ...good, items: [{ sku: "MAT-01", qty: "2" }] }],
    ["no sku", { ...good, items: [{ qty: 1 }] }],
    ["no card", { customer_id: "c-1", items: good.items }],
  ])("400 for a bad body: %s", async (_name, body) => {
    const res = await post(body);
    expect(res.status).toBe(400);
    expect(seen.length).toBe(0);
  });

  it("proxies a valid order to orders and returns 202", async () => {
    const res = await post(good, { "x-trace-id": "t-123" });
    expect(res.status).toBe(202);
    expect(await res.json()).toEqual({ order_id: "o-1", status: "PENDING" });
    expect(res.headers.get("x-trace-id")).toBe("t-123");
    expect(seen.length).toBe(1);
    expect(seen[0].req.method).toBe("POST");
    expect(seen[0].req.url).toBe("/orders");
    expect(seen[0].req.headers["x-trace-id"]).toBe("t-123");
    expect(seen[0].req.headers["x-api-key"]).toBeUndefined();
    expect(JSON.parse(seen[0].body)).toEqual(good);
  });

  it("generates a trace id when the caller sends none", async () => {
    const res = await post(good);
    const id = res.headers.get("x-trace-id");
    expect(id).toMatch(/^[0-9a-f-]{36}$/);
    expect(seen[0].req.headers["x-trace-id"]).toBe(id);
  });

  it("proxies GET /orders/:id and passes the status through", async () => {
    reply = { status: 404, body: { error: "not found" } };
    const res = await fetch(`${base}/orders/o-9`, {
      headers: { "x-api-key": "dev-key" },
    });
    expect(res.status).toBe(404);
    expect(seen[0].req.url).toBe("/orders/o-9");
  });

  it("502 when orders is unreachable", async () => {
    const port = (orders.address() as AddressInfo).port;
    await close(gateway);
    await close(orders);
    await start(`http://127.0.0.1:${port}`);
    const res = await post(good);
    expect(res.status).toBe(502);
    expect(logs.at(-1)).toMatchObject({ level: "error", code: 502 });
  });

  it("writes one JSON log line per request", async () => {
    await post(good, { "x-trace-id": "t-log" });
    const line = logs.find((l) => l.trace_id === "t-log");
    expect(line).toMatchObject({
      level: "info",
      msg: "request",
      method: "POST",
      route: "/orders",
      code: 202,
    });
  });

  it("exposes contract metrics on /metrics", async () => {
    await post(good);
    await post(good, { "x-api-key": "" });
    const text = await (await fetch(`${base}/metrics`)).text();
    expect(text).toContain(
      'http_requests_total{service="gateway",route="/orders",code="202"} 1');
    expect(text).toContain(
      'http_requests_total{service="gateway",route="/orders",code="401"} 1');
    expect(text).toContain("http_request_duration_seconds_bucket");
  });
});
