import express, { type NextFunction, type Request, type Response } from "express";
import { randomUUID } from "node:crypto";
import type { ItemInput, Store } from "./db.js";
import type { Logger } from "./log.js";
import { SERVICE, type Metrics } from "./metrics.js";
import { orderTotal, type Saga } from "./saga.js";

const TRACE_HEADER = "x-trace-id";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const money = (x: number) => Math.round(x * 100) / 100;

interface OrderBody {
  customerId: string;
  cardToken: string;
  items: ItemInput[];
}

function parseBody(b: any): OrderBody | string {
  if (!b || typeof b !== "object") return "body must be a JSON object";
  if (typeof b.customer_id !== "string" || !b.customer_id) {
    return "customer_id is required";
  }
  if (typeof b.card_token !== "string" || !b.card_token) {
    return "card_token is required";
  }
  if (!Array.isArray(b.items) || b.items.length === 0) {
    return "items must be a non-empty list";
  }
  for (const i of b.items) {
    if (typeof i?.sku !== "string" || !i.sku) return "item sku is required";
    if (!Number.isInteger(i.qty) || i.qty < 1) {
      return "item qty must be a positive integer";
    }
  }
  return {
    customerId: b.customer_id,
    cardToken: b.card_token,
    items: b.items.map((i: ItemInput) => ({ sku: i.sku, qty: i.qty })),
  };
}

export function createApp(d: {
  saga: Saga;
  store: Store;
  metrics: Metrics;
  log: Logger;
}) {
  const app = express();
  app.disable("x-powered-by");

  app.use((req: Request, res: Response, next: NextFunction) => {
    const traceId = req.get(TRACE_HEADER) || randomUUID();
    res.locals.traceId = traceId;
    res.set(TRACE_HEADER, traceId);
    const end = d.metrics.httpDuration.startTimer();
    res.on("finish", () => {
      const route = req.route?.path
        ? req.baseUrl + req.route.path
        : "unmatched";
      const labels = { service: SERVICE, route, code: String(res.statusCode) };
      d.metrics.httpRequests.inc(labels);
      const seconds = end(labels);
      if (route === "/metrics" || route === "/healthz") return;
      d.log.info("http request", {
        method: req.method,
        path: req.path,
        code: res.statusCode,
        duration_ms: Math.round(seconds * 1000),
        trace_id: traceId,
      });
    });
    next();
  });
  app.use(express.json({ limit: "64kb" }));

  app.post("/orders", async (req, res) => {
    const body = parseBody(req.body);
    if (typeof body === "string") {
      res.status(400).json({ error: body });
      return;
    }
    const r = await d.saga.start(body, res.locals.traceId);
    if (!r.ok) {
      res.status(400).json({ error: "unknown sku", skus: r.unknownSkus });
      return;
    }
    res.status(202).json({ order_id: r.order.id, status: r.order.status });
  });

  app.get("/orders/:id", async (req, res) => {
    const order = UUID.test(req.params.id)
      ? await d.store.getOrder(req.params.id)
      : null;
    if (!order) {
      res.status(404).json({ error: "order not found" });
      return;
    }
    res.json({
      order_id: order.id,
      status: order.status,
      items: order.items.map((i) => ({
        sku: i.sku,
        qty: i.qty,
        unit_price: money(i.unitPrice),
      })),
      total: money(orderTotal(order.items)),
    });
  });

  app.get("/healthz", async (_req, res) => {
    try {
      await d.store.ping();
      res.json({ status: "ok" });
    } catch {
      res.status(503).json({ status: "db unavailable" });
    }
  });

  app.get("/metrics", async (_req, res) => {
    res.set("content-type", d.metrics.registry.contentType);
    res.send(await d.metrics.registry.metrics());
  });

  app.use((err: any, req: Request, res: Response, _next: NextFunction) => {
    if (err?.type === "entity.parse.failed") {
      res.status(400).json({ error: "invalid JSON" });
      return;
    }
    d.log.error("request failed", {
      method: req.method,
      path: req.path,
      err,
      trace_id: res.locals.traceId,
    });
    res.status(500).json({ error: "internal error" });
  });

  return app;
}
