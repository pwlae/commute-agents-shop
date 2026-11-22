import { describe, expect, it } from "vitest";
import { TOPICS } from "../src/saga.js";
import { approved, declined, setup, timeout } from "./fakes.js";

const input = {
  customerId: "c-1",
  cardToken: "tok_ok",
  items: [
    { sku: "LAMP-02", qty: 2 },
    { sku: "MUG-05", qty: 1 },
  ],
};

async function placed() {
  const t = setup();
  const r = await t.saga.start(input, "trace-1");
  if (!r.ok) throw new Error("not created");
  return { ...t, id: r.order.id };
}

async function count(t: ReturnType<typeof setup>, from: string, to: string) {
  const m = await t.metrics.transitions.get();
  return m.values.find(
    (v) => v.labels.from === from && v.labels.to === to,
  )?.value ?? 0;
}

describe("saga", () => {
  it("creates a PENDING order and asks inventory to reserve", async () => {
    const t = await placed();
    expect(t.store.orders.get(t.id)!.status).toBe("PENDING");
    expect(t.sent).toEqual([{
      topic: TOPICS.reserve,
      key: t.id,
      value: { order_id: t.id, items: input.items },
      traceId: "trace-1",
    }]);
    expect(await count(t, "NEW", "PENDING")).toBe(1);
  });

  it("rejects unknown skus without publishing", async () => {
    const t = setup();
    const r = await t.saga.start({
      ...input, items: [{ sku: "NOPE-99", qty: 1 }],
    });
    expect(r).toEqual({ ok: false, unknownSkus: ["NOPE-99"] });
    expect(t.sent).toEqual([]);
  });

  it("happy path: reserved + approved -> CONFIRMED and commit", async () => {
    const t = await placed();
    t.payments.post.mockResolvedValue(approved());
    await t.saga.onReserved({ order_id: t.id });

    expect(t.payments.post).toHaveBeenCalledOnce();
    const [path, body, key] = t.payments.post.mock.calls[0];
    expect(path).toBe("/charges");
    expect(key).toBe(`order-${t.id}`);
    expect(body).toEqual({
      order_id: t.id,
      amount_cents: 8100,
      idempotency_key: `order-${t.id}`,
    });

    const o = t.store.orders.get(t.id)!;
    expect(o.status).toBe("CONFIRMED");
    expect(o.chargeId).toBe("ch_1");
    expect(t.sent.at(-1)).toMatchObject({
      topic: TOPICS.commit,
      key: t.id,
      value: { order_id: t.id, items: input.items },
    });
    expect(await count(t, "PENDING", "CONFIRMED")).toBe(1);
  });

  it("declined card -> CANCELLED and release of every item", async () => {
    const t = await placed();
    t.payments.post.mockResolvedValue(declined());
    await t.saga.onReserved({ order_id: t.id });

    expect(t.store.orders.get(t.id)!.status).toBe("CANCELLED");
    const release = t.sent.filter((m) => m.topic === TOPICS.release);
    expect(release).toEqual([{
      topic: TOPICS.release,
      key: t.id,
      value: { order_id: t.id, items: input.items },
      traceId: "trace-from-client",
    }]);
    expect(t.sent.some((m) => m.topic === TOPICS.commit)).toBe(false);
    expect(await count(t, "PENDING", "CANCELLED")).toBe(1);
  });

  it("payments unreachable -> CANCELLED and release", async () => {
    const t = await placed();
    t.payments.post.mockRejectedValue(timeout());
    await t.saga.onReserved({ order_id: t.id });

    expect(t.store.orders.get(t.id)!.status).toBe("CANCELLED");
    expect(t.sent.at(-1)!.topic).toBe(TOPICS.release);
  });

  it("out of stock -> REJECTED, no charge", async () => {
    const t = await placed();
    await t.saga.onRejected({ order_id: t.id, reason: "out of stock" });

    expect(t.store.orders.get(t.id)!.status).toBe("REJECTED");
    expect(t.payments.post).not.toHaveBeenCalled();
    expect(t.sent.map((m) => m.topic)).toEqual([TOPICS.reserve]);
    expect(await count(t, "PENDING", "REJECTED")).toBe(1);
  });

  it("ignores events for orders that are no longer PENDING", async () => {
    const t = await placed();
    t.payments.post.mockResolvedValue(approved());
    await t.saga.onReserved({ order_id: t.id });
    await t.saga.onRejected({ order_id: t.id });
    await t.saga.onReserved({ order_id: "missing" });

    expect(t.store.orders.get(t.id)!.status).toBe("CONFIRMED");
    expect(t.payments.post).toHaveBeenCalledOnce();
  });
});
