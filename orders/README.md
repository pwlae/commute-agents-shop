# orders

TypeScript (Node 22, Express, kafkajs, pg). Owns the order saga.

```
POST /orders  -> PENDING, publishes inventory.reserve.v1
  inventory.rejected.v1 -> REJECTED
  inventory.reserved.v1 -> POST payments /charges
    approved -> CONFIRMED, publishes inventory.commit.v1
    declined -> CANCELLED, publishes inventory.release.v1
```

## HTTP (:3001, internal)

- `POST /orders` `{"customer_id", "items": [{"sku", "qty"}], "card_token"}`
  → `202 {"order_id", "status": "PENDING"}`; `400` on a bad body or an
  unknown sku.
- `GET /orders/{id}` → `{"order_id", "status", "items", "total"}`.
- `GET /healthz`, `GET /metrics`.

Payments is called through `@shop/http` (`libs/http-ts`), 2 s timeout.

## Kafka

Consumer group `orders`: `inventory.reserved.v1`, `inventory.rejected.v1`.
Produces `inventory.reserve.v1`, `inventory.commit.v1`,
`inventory.release.v1`. Key = order id, value = JSON
`{"order_id", "items"}`, trace id in the `x-trace-id` header.

## Database

`orders` database, migrations in `migrations/` (golang-migrate, run by the
`orders-migrate` job in compose). Tables: `orders`, `order_items`,
`products`.

## Metrics

`http_requests_total{service,route,code}`,
`http_request_duration_seconds`, `orders_saga_transitions_total{from,to}`,
`orders_pending_oldest_seconds`.

## Logs

logfmt on stdout: `time=... level=info service=orders msg="..." key=value`.

## Configuration

| Variable | Default |
| -- | -- |
| `PORT` | `3001` |
| `DATABASE_URL` | `postgres://orders:orders@postgres:5432/orders` |
| `KAFKA_BROKERS` | `kafka:9092` |
| `PAYMENTS_URL` | `http://payments:8000` |
| `LOG_LEVEL` | `info` |

## Develop

```sh
(cd ../libs/http-ts && npm ci && npm run build)
npm ci
npm test
```

Image (build context is the repository root):

```sh
docker build -f orders/Dockerfile .
```
