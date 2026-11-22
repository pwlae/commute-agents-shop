// The order saga.
//
//   PENDING --reserve--> inventory
//     rejected            -> REJECTED
//     reserved -> charge  -> approved: CONFIRMED + commit
//                         -> declined: CANCELLED + release
import { randomUUID } from "node:crypto";
import type { Response } from "@shop/http";
import type { Item, ItemInput, NewOrder, Order, Status, Store } from "./db.js";
import type { Logger } from "./log.js";
import type { Metrics } from "./metrics.js";

export const TOPICS = {
  reserve: "inventory.reserve.v1",
  reserved: "inventory.reserved.v1",
  rejected: "inventory.rejected.v1",
  release: "inventory.release.v1",
  commit: "inventory.commit.v1",
} as const;

export interface Bus {
  publish(
    topic: string,
    key: string,
    value: unknown,
    traceId?: string,
  ): Promise<void>;
}

export interface Payments {
  post(
    path: string,
    json: unknown,
    idempotencyKey?: string,
    opts?: { traceId?: string },
  ): Promise<Response>;
}

export interface SagaDeps {
  store: Store;
  bus: Bus;
  payments: Payments;
  log: Logger;
  metrics?: Pick<Metrics, "transitions">;
}

export function orderTotal(items: Item[]): number {
  return items.reduce((sum, i) => sum + i.qty * i.unitPrice, 0);
}

export function amountCents(items: Item[]): number {
  return Math.floor(orderTotal(items) * 100);
}

const lines = (items: Item[]): ItemInput[] =>
  items.map(({ sku, qty }) => ({ sku, qty }));

export class Saga {
  constructor(private d: SagaDeps) {}

  async start(
    input: Omit<NewOrder, "id">,
    traceId?: string,
  ) {
    const id = randomUUID();
    const res = await this.d.store.createOrder({ id, ...input });
    if (!res.ok) return res;
    this.count("NEW", "PENDING");
    this.d.log.info("order created", {
      order_id: id,
      customer_id: input.customerId,
      items: input.items.length,
      trace_id: traceId,
    });
    await this.d.bus.publish(
      TOPICS.reserve,
      id,
      { order_id: id, items: lines(res.order.items) },
      traceId,
    );
    return res;
  }

  async onReserved(msg: { order_id: string }, traceId?: string) {
    const order = await this.d.store.getOrder(msg.order_id);
    if (!order || order.status !== "PENDING") {
      this.d.log.info("reserved event ignored", {
        order_id: msg.order_id,
        status: order?.status ?? "missing",
        trace_id: traceId,
      });
      return;
    }

    const attempt = await this.d.store.nextChargeAttempt(order.id);
    const key = attempt > 1
      ? `order-${order.id}-${attempt}`
      : `order-${order.id}`;
    const amount = amountCents(order.items);

    let res: Response | null = null;
    try {
      res = await this.d.payments.post(
        "/charges",
        { order_id: order.id, amount_cents: amount, idempotency_key: key },
        key,
        { traceId },
      );
    } catch (err) {
      this.d.log.warn("charge call failed", {
        order_id: order.id,
        attempt,
        err,
        trace_id: traceId,
      });
    }

    let charge: { charge_id?: string; status?: string } = {};
    if (res && res.status >= 200 && res.status < 300) {
      charge = res.json();
    } else if (res) {
      this.d.log.warn("charge call failed", {
        order_id: order.id,
        attempt,
        code: res.status,
        trace_id: res.traceId,
      });
    }
    const trace = res?.traceId ?? traceId;

    if (charge.status === "approved") {
      if (await this.move(order, "CONFIRMED", trace, charge.charge_id)) {
        await this.d.bus.publish(
          TOPICS.commit,
          order.id,
          { order_id: order.id, items: lines(order.items) },
          trace,
        );
      }
      return;
    }
    if (await this.move(order, "CANCELLED", trace, charge.charge_id)) {
      await this.d.bus.publish(
        TOPICS.release,
        order.id,
        { order_id: order.id, items: lines(order.items) },
        trace,
      );
    }
  }

  async onRejected(
    msg: { order_id: string; reason?: string },
    traceId?: string,
  ) {
    const order = await this.d.store.getOrder(msg.order_id);
    if (!order || order.status !== "PENDING") {
      this.d.log.info("rejected event ignored", {
        order_id: msg.order_id,
        status: order?.status ?? "missing",
        trace_id: traceId,
      });
      return;
    }
    await this.move(order, "REJECTED", traceId, undefined, msg.reason);
  }

  private async move(
    order: Order,
    to: Status,
    traceId?: string,
    chargeId?: string,
    reason?: string,
  ): Promise<boolean> {
    const moved = await this.d.store.setStatus(
      order.id, order.status, to, chargeId,
    );
    if (!moved) {
      this.d.log.warn("status changed underneath", {
        order_id: order.id,
        from: order.status,
        to,
        trace_id: traceId,
      });
      return false;
    }
    this.count(order.status, to);
    this.d.log.info("order status", {
      order_id: order.id,
      from: order.status,
      to,
      charge_id: chargeId,
      reason,
      trace_id: traceId,
    });
    return true;
  }

  private count(from: string, to: string) {
    this.d.metrics?.transitions.inc({ from, to });
  }
}
