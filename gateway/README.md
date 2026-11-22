# gateway

The shop's front door, in TypeScript (Express). It is the only
service exposed on the host, on port 8080.

- Checks the `X-Api-Key` header (`API_KEYS`, comma-separated,
  default `dev-key`): missing or unknown key → `401`.
- Validates the body of `POST /orders` → `400` with `details`.
- Proxies `POST /orders` and `GET /orders/{id}` to orders
  (`ORDERS_URL`, default `http://orders:3001`) through the shared
  client `@shop/http`, and passes the status and body through.
  Orders unreachable → `502`, timed out → `504`, breaker open → `503`.
- Forwards `x-trace-id` (generates one if absent) and returns it.
- Logs one JSON object per line.
- `GET /metrics` (Prometheus), `GET /healthz`.

```sh
curl -s -X POST localhost:8080/orders \
  -H 'X-Api-Key: dev-key' -H 'content-type: application/json' \
  -d '{"customer_id":"c-1","items":[{"sku":"MAT-01","qty":2}],"card_token":"tok_ok"}'
```

Tests (build the shared client first):

```sh
(cd ../libs/http-ts && npm ci && npm run build)
npm ci && npm test
```

Image (context is the repo root): `docker build -f gateway/Dockerfile .`
