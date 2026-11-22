# Planted bugs (maintainer notes)

> **Spoilers.** This file explains the bugs that the course exercises ask
> you to find. If you are doing the exercises, stop reading here: start
> from an alert, a ticket or a log line instead.

The bugs are in the code all the time; nothing switches them on, except
bug 6, which needs a bad build. Each one has a trigger:

```sh
docker compose down -v && docker compose up -d --build
scripts/wait-healthy.sh
scripts/trigger.sh <1-6>      # --no-wait: trigger only
```

`trigger.sh` reproduces the bug through the public API (or Kafka, or the
card provider's chaos switch) and then waits up to 2 minutes for its alert
or signal. Use a fresh stack for every trigger: several of them leave the
stack broken, and stock is never refilled. Each trigger was run 3 times on
a fresh stack; times below are from those runs.

| # | Bug | Trigger | Signal |
| - | --- | ------- | ------ |
| 1 | Double charge on replay | orders killed while a charge is in flight | alert `DuplicateCharge` |
| 2 | Stock drift on a multi-item decline | 3-item order with `tok_decline` | alert `StockDrift` |
| 3 | Captured for a failed order | card provider latency 3 s | alert `CapturedForFailedOrder` |
| 4 | Poison message stalls inventory | `not-json` on `inventory.reserve.v1` | alert `InventoryConsumerLag` (later `SagaStuck`) |
| 5 | The 19.98 desk mat | 2 × `MAT-01` | `total` 19.98, charge 1997 cents (no alert) |
| 6 | Bad inventory deploy | `scripts/deploy.sh inventory --bad` | every order `REJECTED` (no alert) |

## 1. Double charge on replay

**Where:** `orders/src/saga.ts`, `onReserved`.

**What happens:** each call to `onReserved` bumps `orders.charge_attempts`
and, from the second attempt on, sends the charge with the idempotency key
`order-<id>-<attempt>` instead of `order-<id>`. Kafka delivers at least
once: if orders dies after payments accepted the charge but before the
`inventory.reserved.v1` offset is committed, the event comes again after
the restart. The order is still `PENDING`, so the saga charges again with a
new key, and payments, which deduplicates by key, takes the money twice.

**Trigger:** card provider latency 1.5 s (below the 2 s timeout), place one
`tok_ok` order, `docker compose kill orders` as soon as the charge shows in
payments, start orders again. The event is redelivered when the consumer
group rebalances (about 30 s, the old member's session timeout).

**Signal:** `payments_duplicate_charges_total` goes up, `DuplicateCharge`
fires 31–34 s after the trigger. Two approved charges for one order in
`GET /charges?order_id=`. The order ends `CONFIRMED`.

**Log lines:**

```
payments-1 | 2026-11-22 09:34:22,441 WARNING payments.api second approved charge for order_id=a5533c47-1f95-4280-b074-f17b1006d4c0 charge_id=ch_a81fa315653142338cb0124d trace_id=b3c3982b-1ccf-4226-8893-2a334b8507fb
payments-1 | 2026-11-22 09:33:56,301 WARNING payments.reconciler orders unavailable, keeping last value: connect failed: http://orders:3001/orders/a5533c47-1f95-4280-b074-f17b1006d4c0
```

**Fix:** derive the key from the business id only (`order-<id>`) on every
attempt; a repeat then returns the first charge.

## 2. Stock drift on a multi-item decline

**Where:** `inventory/internal/store/postgres.go`, `move` (used by release
and commit).

**What happens:** the `UPDATE reservations ... RETURNING sku, qty` runs
through `QueryRow`. Postgres updates every held reservation of the order,
but `QueryRow` reads only the first returned row, so the stock counters are
moved back for one SKU only. For the other SKUs the reservation says
`released` while `stock.reserved` still counts it: those units are lost
until someone fixes the rows by hand. The same happens on commit of a
confirmed multi-SKU order. Single-SKU orders (all of the traffic profile and
the smoke test) are not affected, and the in-memory store used by the unit
tests is correct; the Postgres store test only releases a single-SKU order.

**Trigger:** one order of `MAT-01`, `LAMP-02`, `MUG-05` (1 each) with
`tok_decline`.

**Signal:** `inventory_stock_drift` = 2 (for `LAMP-02` and `MUG-05`,
`stock.reserved` is 1 with no held reservation behind it);
`StockDrift` fires 42–57 s after the trigger (`for: 30s`). The order is
correctly `CANCELLED`; `GET /stock/LAMP-02` shows `reserved: 1` forever.

**Log lines** (no error; the clue is 3 reserved, 1 released):

```
inventory-1 | 2026/11/22 09:28:28 INFO reserved 3 items for order 30d976c7-07fa-4a02-b0f2-492fba0a80e4 trace=e1ff5f0cc84c464a
inventory-1 | 2026/11/22 09:28:28 INFO release order 30d976c7-07fa-4a02-b0f2-492fba0a80e4: 1 items trace=28c13a9152f4b190
```

**Fix:** collect all returned rows (`tx.Query` + `pgx.CollectRows`), and
add a multi-SKU case to the Postgres store test.

## 3. Captured for a failed order

**Where:** `orders/src/saga.ts` (`onReserved`) and the 2 s charge timeout in
`orders/src/main.ts`, against the 5 s card-provider timeout in payments.

**What happens:** when the charge call times out, the saga treats it like a
decline: `CANCELLED` and `inventory.release.v1`. Payments is still waiting
for the card provider and approves the charge a second later. Nobody
refunds it.

**Trigger:** `POST /admin/chaos {"latency_ms": 3000}` on the card provider,
one `tok_ok` order.

**Signal:** the reconciler (every 15 s) sets
`payments_captured_for_failed_orders` to 1; `CapturedForFailedOrder` fires
32–37 s after the trigger.

**Log lines:**

```
orders-1 | time=2026-11-22T09:35:32.159Z level=warn service=orders msg="charge call failed" order_id=bb43913f-32ae-4604-bee9-be5c4a4adb22 attempt=1 err="timeout after 2000 ms: http://payments:8000/charges"
payments-1 | 2026-11-22 09:35:46,967 WARNING payments.reconciler captured for failed order charge_id=ch_a959735335fb402cb1b5a47e order_id=bb43913f-32ae-4604-bee9-be5c4a4adb22
```

**Fix:** a timeout is "unknown", not "declined": keep the order `PENDING`
and retry with the same idempotency key, or ask payments for the charge
before compensating; refund if the order is cancelled after all.

## 4. Poison message stalls inventory

**Where:** `inventory/internal/consumer/kafka.go` (`consume`, `Run`) and
`handler.go`.

**What happens:** a value that is not JSON makes `Handle` return an error.
The consumer treats every error as "try again": it closes the client,
waits 2 s and starts from the last committed offset, which is the same bad
message, forever. `inventory.dlq.v1` exists but nothing writes to it. The
orders behind it on that partition (and often the others, which share the
client) wait. `inventory_consumer_lag` is measured from the group's
committed offsets, not from its members, so it shows the backlog even
while the consumer keeps leaving and rejoining the group.

**Trigger:** produce `not-json` to each of the 3 partitions of
`inventory.reserve.v1` (keys `bad-4`, `bad-0`, `bad-3` hash to partitions
0, 1, 2), then 30 orders.

**Signal:** `inventory_consumer_lag` climbs (33: 3 bad values + 30
orders); `InventoryConsumerLag` fires 72–83 s after the trigger (`for: 1m`). Orders
stay `PENDING`, so `SagaStuck` follows after 2 minutes.

**Log line** (every 2–7 s):

```
inventory-1 | 2026/11/22 09:37:18 ERROR consumer stopped: decode inventory.reserve.v1: invalid character 'o' in literal null (expecting 'u'); restarting in 2s
```

**Fix:** a message that cannot be decoded will never succeed: publish it to
`inventory.dlq.v1`, commit, move on. Keep retrying only for errors that can
pass (database down).

## 5. The 19.98 desk mat

**Where:** `orders/migrations/0003_create_products.up.sql` and
`amountCents` in `orders/src/saga.ts`.

**What happens:** `products.unit_price` is `real` (4-byte float). 9.99
stored as a `real` is 9.98999977111816…; copying it into the
`double precision` column `order_items.unit_price` keeps that value. Two
mats are 19.9799995422…, times 100 is 1997.99995…, and `amountCents` uses
`Math.floor`, so payments is asked for **1997** cents. `GET /orders/{id}`
rounds to cents and shows **19.98**. (In pure double precision
2 × 9.99 × 100 is exactly 1998.0; it is the `real` column plus the floor
that loses the cent.)

**Trigger:** one order of 2 × `MAT-01`, `tok_ok`.

**Signal:** none fires. `total` 19.98 against `amount_cents` 1997 in
payments:

```
payments-1 | 2026-11-22 09:39:19,089 INFO payments.api charge approved charge_id=ch_00d272af52784828b5369331 order_id=bfee3bb4-73fd-4446-8f7c-805bc0eb4223 amount_cents=1997 trace_id=f399a585-460a-4a2a-8d08-885ea00eaa99
```

**Fix:** money in integer cents end to end (a new migration, never an edit
of `0003`); until then, round instead of floor. Ticket SHOP-101.

## 6. Bad inventory deploy

**Where:** `inventory/internal/consumer/sku.go` and the `BAD_SKU_CASE`
build argument in `inventory/Dockerfile`.

**What happens:** `scripts/deploy.sh inventory --bad` builds inventory with
`skuCase=lower`, so every SKU in a command is lower-cased before the stock
lookup. The catalogue is upper case: every reservation is rejected as
`unknown sku mat-01` and every order ends `REJECTED`. The deploy is
recorded in `ops/deploys.jsonl` with the note "inventory: normalise SKU
case on lookup". Gateway answers `202` and `GET` `200` as usual, so
`GatewayHighErrorRate` does not fire. This is the "bad deploy just before
the spike" case for triage.

**Trigger:** the bad deploy, then 10 ordinary orders.

**Signal:** `orders_saga_transitions_total{from="PENDING",to="REJECTED"}`
jumps (+10 within one scrape, 15 s after the orders), the REJECTED line on
the saga dashboard.

**Log lines:**

```
inventory-1 | 2026/11/22 09:40:49 WARN rejected order 44cafa72-0bc9-4d37-ae4f-c55120bd5b11: unknown sku chair-03 trace=…
orders-1 | time=2026-11-22T09:40:49.777Z level=info service=orders msg="order status" order_id=44cafa72-0bc9-4d37-ae4f-c55120bd5b11 from=PENDING to=REJECTED reason="unknown sku chair-03"
```

**Fix:** roll back: `scripts/deploy.sh inventory` (a normal build).

## Not bugs, but quirks you will meet

- `orders/0004` renames `customer` to `customer_id` in place;
  `inventory/0003` creates an index without `CONCURRENTLY`.
- Four log formats: gateway JSON, orders logfmt, payments Python default,
  inventory plain text.
- Inventory reads and writes the `trace-id` header, everything else uses
  `x-trace-id`, so traces break between orders and inventory.
