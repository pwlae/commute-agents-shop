# payments

Python 3.13, FastAPI, Postgres (database `payments`). Port 8000.

| Route | Body | Reply |
| --- | --- | --- |
| `POST /charges` | `order_id`, `amount_cents`, `card_token`, `idempotency_key` (or the `idempotency-key` header) | `charge_id`, `status` (`approved` / `declined`), `order_id`, `amount_cents`, `decline_reason` |
| `GET /charges/{charge_id}` | | the charge |
| `GET /charges?order_id=` | | all charges for an order |
| `POST /refunds` | `charge_id` | `refund_id`, `charge_id`, `amount_cents`, `status: refunded` |
| `GET /healthz`, `GET /metrics` | | |

A repeat of `POST /charges` with the same idempotency key returns the
first charge and does not call the card provider again. The same key with
a different order or amount is `409`, and so is a repeat while the first
call is still running. If the card provider cannot be reached, the reply
is `502` and nothing is stored, so the caller can retry with the same key.

The card provider is called through `shop_http` with a 5 s timeout.
A reconciler runs every `RECONCILE_EVERY_S` seconds (15 by default): it
asks orders for the status of each approved, unrefunded charge from the
last day and sets `payments_captured_for_failed_orders`.

Metrics: `http_requests_total`, `http_request_duration_seconds`,
`payments_charges_total{status}`, `payments_duplicate_charges_total`
(an approved charge for an order that already had one),
`payments_captured_for_failed_orders`, `card_provider_errors_total{reason}`.

Migrations are golang-migrate files in `migrations/`. The service applies
them at start and keeps the same `schema_migrations` table, so the
`migrate` CLI works on the same database.

Environment: `DATABASE_URL`, `CARD_PROVIDER_URL`, `ORDERS_URL`,
`RECONCILE_EVERY_S`, `PORT`.

## Tests

```sh
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-test.txt ../libs/http-py
pytest
```

Tests use an in-memory store. Set `PAYMENTS_TEST_DSN` to an empty
Postgres database to run the same tests against Postgres.
