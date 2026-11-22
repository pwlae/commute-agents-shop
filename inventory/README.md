# inventory

Go service that owns stock. It takes commands from Kafka, keeps stock and
reservations in the `inventory` Postgres database, and answers stock
lookups over HTTP on `:8200`.

## Kafka

Consumer group `inventory`. Values are JSON, key is the order id.

| Topic | Direction | Value |
| --- | --- | --- |
| `inventory.reserve.v1` | in | `{"order_id", "items": [{"sku", "qty"}]}` |
| `inventory.release.v1` | in | `{"order_id", "items"?}` (no items: release all the order holds) |
| `inventory.commit.v1` | in | `{"order_id"}` |
| `inventory.reserved.v1` | out | `{"order_id", "items"}` |
| `inventory.rejected.v1` | out | `{"order_id", "reason"}` |

Each command runs once per order: a repeat is recorded in
`processed_commands` and skipped.

## HTTP

- `GET /stock/{sku}` → `{"sku", "available", "reserved"}`, 404 for an unknown SKU
- `GET /healthz`
- `GET /metrics`: `http_requests_total`, `http_request_duration_seconds`,
  `inventory_stock_drift`, `inventory_consumer_lag`

## Database

Migrations in `migrations/` (golang-migrate format) run at start.
`0005` seeds the catalogue with 50 of each SKU.

## Config

| Variable | Default |
| --- | --- |
| `PORT` | `8200` |
| `DATABASE_URL` | `postgres://inventory:inventory@postgres:5432/inventory?sslmode=disable` |
| `KAFKA_BROKERS` | `kafka:9092` |

## Tests

```sh
go test ./...
```

The Postgres store test runs when `INVENTORY_TEST_DB` points at an empty
database, for example
`pgx5://inventory:inventory@127.0.0.1:5432/inventory?sslmode=disable`.

Image (from the repo root): `docker build -f inventory/Dockerfile .`
