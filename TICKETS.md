# Tickets

Small work tickets for the shop, written the way a product team writes
them. Pick one, hand it to your agent, and see what comes back as a pull
request. Each one says what done looks like; how to get there is up to you.

| Ticket | Title | Services |
| ------ | ----- | -------- |
| SHOP-101 | Two desk mats cost 19.97 | orders |
| SHOP-102 | List a customer's orders | gateway, orders |
| SHOP-103 | Restock a SKU | inventory |
| SHOP-104 | Say why an order was cancelled | orders |
| SHOP-105 | Store prices in cents | orders |
| SHOP-106 | Speed up the stock drift query | inventory |
| SHOP-107 | Don't get stuck on a bad message | inventory |
| SHOP-108 | Follow one order through the logs | inventory, orders |
| SHOP-109 | Show stock left on the order page | orders, inventory |
| SHOP-110 | Charges in more than one currency | payments, orders, gateway |
| SHOP-111 | Rate limit per API key | gateway |
| SHOP-112 | Count refunds | payments |

---

## SHOP-101 · Two desk mats cost 19.97

A customer bought two desk mats (`MAT-01`, 9.99 each). The order page says
19.98, the card statement says 19.97.

**Done when**
- An order of 2 × `MAT-01` is charged 1998 cents and shows 19.98.
- A test pins it, and other quantities of every catalogue item are
  charged exactly qty × price.

## SHOP-102 · List a customer's orders

Support wants to see every order of one customer, newest first.

**Done when**
- `GET /orders?customer_id=c-1` on the gateway returns
  `[{"order_id", "status", "total", "created_at"}]`, newest first, at most
  50.
- It needs the API key like every other route.
- Orders and gateway READMEs show the new route.

## SHOP-103 · Restock a SKU

Stock only goes down; after a while every order is rejected. Operations
wants to add stock without restarting anything.

**Done when**
- `POST /stock/{sku}/restock {"qty": 20}` on inventory adds to `available`
  (and to what the drift check expects), `404` for an unknown SKU, `400`
  for `qty <= 0`.
- `inventory_stock_drift` stays 0 after a restock.

## SHOP-104 · Say why an order was cancelled

`GET /orders/{id}` says `CANCELLED` but not why. Support has to dig in the
payments logs.

**Done when**
- Orders keeps the reason (`card_declined`, `issuer_declined`, `timeout`,
  ...) and `GET /orders/{id}` returns it as `reason` when the order is
  `CANCELLED` or `REJECTED`.
- Existing orders still read fine.

## SHOP-105 · Store prices in cents

Prices are floating point. We want integer cents in the database, without
taking the shop down.

**Done when**
- `products` and `order_items` have integer cent columns, filled for
  existing rows, and the code reads and writes them.
- The old columns stay until a later release removes them.
- `docker compose down -v && docker compose up -d --build` and an upgrade
  of a running stack both work.

## SHOP-106 · Speed up the stock drift query

The drift check scans `reservations` every 10 seconds. With a year of
orders that will hurt.

**Done when**
- An index supports the drift query (check with `EXPLAIN`).
- Adding it does not block writes to `reservations` on a busy database.

## SHOP-107 · Don't get stuck on a bad message

If a message on an inventory command topic cannot be read, inventory stops
processing everything behind it.

**Done when**
- A message that cannot be decoded ends up on `inventory.dlq.v1` with the
  topic, partition, offset and error, and the consumer moves on.
- Errors that can pass (database down) are still retried.
- A test covers both.

## SHOP-108 · Follow one order through the logs

Support takes the trace id from the gateway log and searches for it. They
find the order in orders and payments, but never in inventory.

**Done when**
- One trace id, sent to the gateway, shows up in the gateway, orders,
  inventory and payments logs for that order.
- Messages already in Kafka with the old header still work.

## SHOP-109 · Show stock left on the order page

Product wants `GET /orders/{id}` to say how many of each item are left, so
the app can show "only 3 left".

**Done when**
- Each item in `GET /orders/{id}` has `left` from inventory's
  `GET /stock/{sku}`.
- If inventory is slow or down, the order still loads (without `left`)
  within a second.

## SHOP-110 · Charges in more than one currency

We start selling in EUR next quarter. Today every amount is assumed to be
GBP.

**Done when**
- Orders, the charge request and the charge record carry a `currency`
  (ISO 4217, default `GBP`); payments rejects anything but `GBP` and
  `EUR`.
- An old orders build keeps working against the new payments while they
  are deployed one after the other.
- `GET /orders/{id}` through the gateway shows the currency.

## SHOP-111 · Rate limit per API key

One partner's script sent 300 orders a second last week.

**Done when**
- The gateway allows at most `RATE_LIMIT_PER_S` requests a second per API
  key (default 20) and answers `429` with `Retry-After` above that.
- `/healthz` and `/metrics` are not limited.

## SHOP-112 · Count refunds

Finance wants a graph of refunds next to charges.

**Done when**
- Payments exports a counter of refunds and the amount refunded.
- The shop overview dashboard shows both next to charges.
