import { Counter, Histogram, Registry, collectDefaultMetrics } from "prom-client";

export function makeMetrics() {
  const registry = new Registry();
  collectDefaultMetrics({ register: registry });
  const labelNames = ["service", "route", "code"] as const;
  const requests = new Counter({
    name: "http_requests_total",
    help: "HTTP requests handled",
    labelNames,
    registers: [registry],
  });
  const duration = new Histogram({
    name: "http_request_duration_seconds",
    help: "HTTP request duration in seconds",
    labelNames,
    buckets: [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5],
    registers: [registry],
  });
  return { registry, requests, duration };
}
export type Metrics = ReturnType<typeof makeMetrics>;
