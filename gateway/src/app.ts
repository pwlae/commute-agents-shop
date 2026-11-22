import { randomUUID } from "node:crypto";
import express, { NextFunction, Request, Response } from "express";
import {
  CircuitOpenError,
  ConnectError,
  TimeoutError,
  client,
  type Response as UpstreamResponse,
} from "@shop/http";
import { Logger, jsonLogger } from "./log.js";
import { makeMetrics } from "./metrics.js";
import { validateOrder } from "./validate.js";

export interface GatewayOptions {
  ordersUrl: string;
  apiKeys: string[];
  log?: Logger;
  timeoutMs?: number;
}

const TRACE = "x-trace-id";

export function createApp(opts: GatewayOptions) {
  const log = opts.log ?? jsonLogger();
  const metrics = makeMetrics();
  const keys = new Set(opts.apiKeys);
  const orders = client(opts.ordersUrl, {
    timeout_ms: opts.timeoutMs ?? 2000,
  });
  const app = express();
  app.disable("x-powered-by");

  // Trace id, request log line and metrics for every request.
  app.use((req, res, next) => {
    const traceId = req.get(TRACE) || randomUUID();
    res.locals.traceId = traceId;
    res.set(TRACE, traceId);
    const start = process.hrtime.bigint();
    res.on("finish", () => {
      const sec = Number(process.hrtime.bigint() - start) / 1e9;
      const route = req.route?.path
        ? req.baseUrl + req.route.path
        : "other";
      const labels = {
        service: "gateway",
        route,
        code: String(res.statusCode),
      };
      metrics.requests.inc(labels);
      metrics.duration.observe(labels, sec);
      if (route === "/metrics" || route === "/healthz") return;
      log(res.statusCode >= 500 ? "error" : "info", "request", {
        method: req.method,
        path: req.originalUrl,
        route,
        code: res.statusCode,
        duration_ms: Math.round(sec * 1000),
        trace_id: traceId,
        ...(res.locals.error ? { error: res.locals.error } : {}),
      });
    });
    next();
  });

  app.get("/healthz", (_req, res) => {
    res.json({ status: "ok" });
  });

  app.get("/metrics", async (_req, res) => {
    res.set("content-type", metrics.registry.contentType);
    res.send(await metrics.registry.metrics());
  });

  const requireKey = (req: Request, res: Response, next: NextFunction) => {
    const key = req.get("x-api-key");
    if (!key || !keys.has(key)) {
      res.status(401).json({ error: "missing or invalid X-Api-Key" });
      return;
    }
    next();
  };

  const send = (res: Response, up: UpstreamResponse) => {
    res.status(up.status);
    res.type(up.headers.get("content-type") ?? "application/json");
    res.send(up.body);
  };

  const fail = (res: Response, err: unknown) => {
    const [code, msg] =
      err instanceof CircuitOpenError ? [503, "orders unavailable"]
      : err instanceof TimeoutError ? [504, "orders timed out"]
      : err instanceof ConnectError ? [502, "orders unreachable"]
      : [500, "internal error"];
    res.locals.error = (err as Error)?.message ?? String(err);
    res.status(code).json({ error: msg });
  };

  app.post(
    "/orders",
    requireKey,
    express.json({ limit: "64kb" }),
    async (req, res) => {
      const errors = validateOrder(req.body);
      if (errors.length) {
        res.status(400).json({ error: "invalid order", details: errors });
        return;
      }
      const { customer_id, items, card_token } = req.body;
      try {
        const up = await orders.post(
          "/orders",
          { customer_id, items, card_token },
          undefined,
          { traceId: res.locals.traceId },
        );
        send(res, up);
      } catch (err) {
        fail(res, err);
      }
    },
  );

  app.get("/orders/:id", requireKey, async (req, res) => {
    try {
      const id = encodeURIComponent(String(req.params.id));
      const up = await orders.get(`/orders/${id}`, {
        traceId: res.locals.traceId,
      });
      send(res, up);
    } catch (err) {
      fail(res, err);
    }
  });

  app.use((_req: Request, res: Response) => {
    res.status(404).json({ error: "not found" });
  });

  // Body parser errors (bad JSON, too large) and anything else.
  app.use((err: any, _req: Request, res: Response, _next: NextFunction) => {
    const status = Number(err?.status) || 500;
    res.locals.error = err?.message ?? String(err);
    res.status(status).json({
      error: status === 400 ? "invalid JSON" : status < 500 ? err.type : "internal error",
    });
  });

  return app;
}
