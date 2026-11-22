import { Counter, Gauge, Histogram, Registry, collectDefaultMetrics } from "prom-client";

export const SERVICE = "orders";

export interface Metrics {
  registry: Registry;
  httpRequests: Counter<"service" | "route" | "code">;
  httpDuration: Histogram<"service" | "route" | "code">;
  transitions: Counter<"from" | "to">;
}

export function createMetrics(
  pendingOldestSeconds: () => Promise<number>,
  defaults = true,
): Metrics {
  const registry = new Registry();
  if (defaults) collectDefaultMetrics({ register: registry });

  const httpRequests = new Counter({
    name: "http_requests_total",
    help: "HTTP requests handled",
    labelNames: ["service", "route", "code"] as const,
    registers: [registry],
  });
  const httpDuration = new Histogram({
    name: "http_request_duration_seconds",
    help: "HTTP request duration",
    labelNames: ["service", "route", "code"] as const,
    buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
    registers: [registry],
  });
  const transitions = new Counter({
    name: "orders_saga_transitions_total",
    help: "Order status changes made by the saga",
    labelNames: ["from", "to"] as const,
    registers: [registry],
  });
  new Gauge({
    name: "orders_pending_oldest_seconds",
    help: "Age of the oldest PENDING order (0 if none)",
    registers: [registry],
    async collect() {
      this.set(await pendingOldestSeconds());
    },
  });

  return { registry, httpRequests, httpDuration, transitions };
}
