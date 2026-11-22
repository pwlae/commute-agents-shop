import { createServer, IncomingMessage, Server } from "node:http";
import { AddressInfo } from "node:net";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CircuitOpenError, ConnectError, TimeoutError, client } from "../src/index.js";

type Reply = { status: number; body?: unknown; delayMs?: number };

let server: Server;
let base: string;
let replies: Reply[];
let seen: IncomingMessage[];

beforeEach(async () => {
  replies = [];
  seen = [];
  server = createServer((req, res) => {
    seen.push(req);
    const r = replies.length > 1 ? replies.shift()! : replies[0];
    setTimeout(() => {
      res.writeHead(r.status, { "content-type": "application/json" });
      res.end(JSON.stringify(r.body ?? {}));
    }, r.delayMs ?? 0);
  });
  await new Promise<void>((ok) => server.listen(0, "127.0.0.1", ok));
  base = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

afterEach(async () => {
  server.closeAllConnections();
  await new Promise((ok) => server.close(ok));
});

describe("@shop/http", () => {
  it("retries on 503 then succeeds", async () => {
    replies = [{ status: 503 }, { status: 503 }, { status: 200, body: { ok: 1 } }];
    const c = client(base, { backoff_ms: 1 });
    const res = await c.get("/x");
    expect(res.status).toBe(200);
    expect(res.json()).toEqual({ ok: 1 });
    expect(seen.length).toBe(3);
  });

  it("returns the last 503 when retries run out", async () => {
    replies = [{ status: 503 }];
    const res = await client(base, { backoff_ms: 1 }).get("/x");
    expect(res.status).toBe(503);
    expect(seen.length).toBe(3);
  });

  it("does not retry on 400", async () => {
    replies = [{ status: 400, body: { error: "bad" } }];
    const res = await client(base, { backoff_ms: 1 }).post("/x", { a: 1 });
    expect(res.status).toBe(400);
    expect(seen.length).toBe(1);
  });

  it("does not retry on 500", async () => {
    replies = [{ status: 500 }];
    const res = await client(base, { backoff_ms: 1 }).get("/x");
    expect(res.status).toBe(500);
    expect(seen.length).toBe(1);
  });

  it("does not retry a timeout", async () => {
    replies = [{ status: 200, delayMs: 300 }];
    const c = client(base, { timeout_ms: 50, backoff_ms: 1 });
    await expect(c.get("/slow")).rejects.toBeInstanceOf(TimeoutError);
    expect(seen.length).toBe(1);
  });

  it("retries connect errors, then throws ConnectError", async () => {
    const dead = createServer();
    await new Promise<void>((ok) => dead.listen(0, "127.0.0.1", ok));
    const port = (dead.address() as AddressInfo).port;
    await new Promise((ok) => dead.close(ok));
    const c = client(`http://127.0.0.1:${port}`, { backoff_ms: 20 });
    const t0 = Date.now();
    await expect(c.get("/x")).rejects.toBeInstanceOf(ConnectError);
    // two retries: 20 ms + 40 ms of backoff
    expect(Date.now() - t0).toBeGreaterThanOrEqual(55);
  });

  it("opens the breaker after 5 failures and rejects fast", async () => {
    replies = [{ status: 503 }];
    const c = client(base, {
      retries: 0,
      breaker: { failures: 5, open_ms: 60000 },
    });
    for (let i = 0; i < 5; i++) {
      expect((await c.get("/x")).status).toBe(503);
    }
    expect(c.breakerState).toBe("open");
    const t0 = Date.now();
    await expect(c.get("/x")).rejects.toBeInstanceOf(CircuitOpenError);
    expect(Date.now() - t0).toBeLessThan(20);
    expect(seen.length).toBe(5);
  });

  it("half-opens after open_ms and closes on success", async () => {
    replies = [{ status: 503 }, { status: 200 }];
    const c = client(base, {
      retries: 0,
      breaker: { failures: 1, open_ms: 30 },
    });
    await c.get("/x");
    await expect(c.get("/x")).rejects.toBeInstanceOf(CircuitOpenError);
    await new Promise((r) => setTimeout(r, 40));
    expect((await c.get("/x")).status).toBe(200);
    expect(c.breakerState).toBe("closed");
  });

  it("4xx does not count as a failure", async () => {
    replies = [{ status: 404 }];
    const c = client(base, { breaker: { failures: 2 } });
    for (let i = 0; i < 4; i++) await c.get("/x");
    expect(c.breakerState).toBe("closed");
  });

  it("forwards x-trace-id and the idempotency key on every attempt", async () => {
    replies = [{ status: 502 }, { status: 200 }];
    const c = client(base, { backoff_ms: 1 });
    const res = await c.post("/charges", { a: 1 }, "order-1", {
      traceId: "trace-abc",
    });
    expect(res.traceId).toBe("trace-abc");
    expect(seen.map((r) => r.headers["x-trace-id"])).toEqual([
      "trace-abc",
      "trace-abc",
    ]);
    expect(seen[1].headers["idempotency-key"]).toBe("order-1");
    expect(seen[1].headers["content-type"]).toBe("application/json");
  });

  it("generates a trace id when absent", async () => {
    replies = [{ status: 200 }];
    const res = await client(base).get("/x");
    expect(res.traceId).toMatch(/^[0-9a-f-]{36}$/);
    expect(seen[0].headers["x-trace-id"]).toBe(res.traceId);
  });
});
