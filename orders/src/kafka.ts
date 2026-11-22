import { Kafka, Partitioners, logLevel, type LogEntry } from "kafkajs";
import type { Logger } from "./log.js";
import { TOPICS, type Bus, type Saga } from "./saga.js";

const TRACE_HEADER = "x-trace-id";
const LEVELS: Record<number, "error" | "warn" | "info" | "debug"> = {
  [logLevel.ERROR]: "error",
  [logLevel.WARN]: "warn",
  [logLevel.INFO]: "info",
  [logLevel.DEBUG]: "debug",
};

export function createKafka(brokers: string[], log: Logger): Kafka {
  return new Kafka({
    clientId: "orders",
    brokers,
    logLevel: logLevel.WARN,
    logCreator: () => (e: LogEntry) => {
      const { message, timestamp, logger, ...rest } = e.log;
      log[LEVELS[e.level] ?? "info"](`kafka: ${message}`, {
        component: e.namespace,
        broker: rest.broker,
        error: rest.error,
      });
    },
  });
}

export async function createBus(kafka: Kafka): Promise<Bus & {
  disconnect(): Promise<void>;
}> {
  const producer = kafka.producer({
    createPartitioner: Partitioners.DefaultPartitioner,
  });
  await producer.connect();
  return {
    async publish(topic, key, value, traceId) {
      await producer.send({
        topic,
        messages: [{
          key,
          value: JSON.stringify(value),
          headers: traceId ? { [TRACE_HEADER]: traceId } : {},
        }],
      });
    },
    disconnect: () => producer.disconnect(),
  };
}

export async function startConsumer(kafka: Kafka, saga: Saga, log: Logger) {
  const consumer = kafka.consumer({ groupId: "orders" });
  await consumer.connect();
  await consumer.subscribe({
    topics: [TOPICS.reserved, TOPICS.rejected],
    // A new group must not skip events written before it first
    // committed an offset (e.g. after a crash on a fresh stack).
    fromBeginning: true,
  });
  await consumer.run({
    eachMessage: async ({ topic, partition, message }) => {
      const traceId = message.headers?.[TRACE_HEADER]?.toString();
      let body: any;
      try {
        body = JSON.parse(message.value?.toString() ?? "");
      } catch {
        log.error("bad message skipped", {
          topic, partition, offset: message.offset,
        });
        return;
      }
      if (typeof body?.order_id !== "string") {
        log.error("message without order_id skipped", {
          topic, partition, offset: message.offset,
        });
        return;
      }
      if (topic === TOPICS.reserved) {
        await saga.onReserved(body, traceId);
      } else if (topic === TOPICS.rejected) {
        await saga.onRejected(body, traceId);
      }
    },
  });
  return consumer;
}
