import { TimeoutError, type Response } from "@shop/http";
import { vi } from "vitest";
import type { CreateResult, NewOrder, Order, Status, Store } from "../src/db.js";
import type { Logger } from "../src/log.js";
import { createMetrics } from "../src/metrics.js";
import { Saga, type Bus } from "../src/saga.js";

const PRICES: Record<string, number> = {
  "MAT-01": 9.99,
  "LAMP-02": 34.5,
  "CHAIR-03": 189,
  "CABLE-04": 4.25,
  "MUG-05": 12,
};

export class MemoryStore implements Store {
  orders = new Map<string, Order & { attempts: number }>();

  async createOrder(o: NewOrder): Promise<CreateResult> {
    const unknownSkus = o.items
      .map((i) => i.sku)
      .filter((s) => !(s in PRICES));
    if (unknownSkus.length) return { ok: false, unknownSkus };
    const order = {
      id: o.id,
      customerId: o.customerId,
      cardToken: o.cardToken,
      status: "PENDING" as Status,
      chargeId: null,
      createdAt: new Date(),
      attempts: 0,
      items: o.items.map((i) => ({ ...i, unitPrice: PRICES[i.sku] })),
    };
    this.orders.set(o.id, order);
    return { ok: true, order: { ...order } };
  }

  async getOrder(id: string) {
    const o = this.orders.get(id);
    return o ? { ...o } : null;
  }

  async setStatus(id: string, from: Status, to: Status, chargeId?: string) {
    const o = this.orders.get(id);
    if (!o || o.status !== from) return false;
    o.status = to;
    if (chargeId) o.chargeId = chargeId;
    return true;
  }

  async nextChargeAttempt(id: string) {
    const o = this.orders.get(id)!;
    return ++o.attempts;
  }

  async pendingOldestSeconds() {
    return 0;
  }

  async ping() {}
}

export function reply(status: number, body: unknown): Response {
  const text = JSON.stringify(body);
  return {
    status,
    headers: new Headers(),
    body: text,
    traceId: "trace-from-client",
    json: <T>() => JSON.parse(text) as T,
  };
}

export const approved = () =>
  reply(200, { charge_id: "ch_1", status: "approved" });
export const declined = () =>
  reply(200, { charge_id: "ch_2", status: "declined" });
export const timeout = () =>
  new TimeoutError("http://payments:8000/charges", 2000);

export const silent: Logger = {
  debug() {}, info() {}, warn() {}, error() {},
};

export function setup() {
  const store = new MemoryStore();
  const sent: { topic: string; key: string; value: any; traceId?: string }[] = [];
  const bus: Bus = {
    publish: vi.fn(async (topic, key, value, traceId) => {
      sent.push({ topic, key, value, traceId });
    }),
  };
  const payments = { post: vi.fn() };
  const metrics = createMetrics(() => store.pendingOldestSeconds(), false);
  const saga = new Saga({ store, bus, payments, log: silent, metrics });
  return { store, bus, sent, payments, metrics, saga };
}
