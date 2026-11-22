import { client } from "@shop/http";
import { createApp } from "./api.js";
import { PgStore } from "./db.js";
import { createBus, createKafka, startConsumer } from "./kafka.js";
import { createLogger, type Level } from "./log.js";
import { createMetrics } from "./metrics.js";
import { Saga } from "./saga.js";

const env = (k: string, d: string) => process.env[k] || d;

const PORT = Number(env("PORT", "3001"));
const DATABASE_URL = env(
  "DATABASE_URL",
  "postgres://orders:orders@postgres:5432/orders",
);
const KAFKA_BROKERS = env("KAFKA_BROKERS", "kafka:9092").split(",");
const PAYMENTS_URL = env("PAYMENTS_URL", "http://payments:8000");

const log = createLogger("orders", env("LOG_LEVEL", "info") as Level);

async function main() {
  const store = PgStore.connect(DATABASE_URL);
  const metrics = createMetrics(() => store.pendingOldestSeconds());
  const kafka = createKafka(KAFKA_BROKERS, log);
  const bus = await createBus(kafka);
  const payments = client(PAYMENTS_URL, { timeout_ms: 2000 });
  const saga = new Saga({ store, bus, payments, log, metrics });
  const consumer = await startConsumer(kafka, saga, log);

  const server = createApp({ saga, store, metrics, log }).listen(PORT, () => {
    log.info("listening", { port: PORT });
  });

  const stop = async (signal: string) => {
    log.info("shutting down", { signal });
    server.close();
    await consumer.disconnect().catch(() => undefined);
    await bus.disconnect().catch(() => undefined);
    await store.end().catch(() => undefined);
    process.exit(0);
  };
  process.on("SIGTERM", () => void stop("SIGTERM"));
  process.on("SIGINT", () => void stop("SIGINT"));
}

main().catch((err) => {
  log.error("startup failed", { err });
  process.exit(1);
});
