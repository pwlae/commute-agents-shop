import { createApp } from "./app.js";
import { jsonLogger } from "./log.js";

const log = jsonLogger();
const port = Number(process.env.PORT ?? 8080);
const ordersUrl = process.env.ORDERS_URL ?? "http://orders:3001";
const apiKeys = (process.env.API_KEYS ?? "dev-key")
  .split(",")
  .map((k) => k.trim())
  .filter(Boolean);

const app = createApp({ ordersUrl, apiKeys, log });
const server = app.listen(port, () => {
  log("info", "listening", { port, orders_url: ordersUrl });
});

for (const sig of ["SIGTERM", "SIGINT"] as const) {
  process.on(sig, () => {
    log("info", "shutting down", { signal: sig });
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 5000).unref();
  });
}
