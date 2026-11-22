# card-provider

A fake card provider for local practice. Port 8100. No real money moves.

- `POST /authorize` `{"token", "amount_cents", "reference"}` →
  `{"authorization_id", "approved", "reason"}`. `tok_ok` approves,
  `tok_decline` declines, any other token is declined as `unknown_token`.
- `POST /admin/chaos` `{"latency_ms": int, "decline_rate": float,
  "error_rate": float}` sets how slow and flaky it is (fields left out
  reset to 0). `GET /admin/chaos` shows the current setting.
- `GET /healthz`, `GET /metrics`.

Tests: `pip install -r requirements.txt pytest==9.2.0 httpx2==2.14.0`, then `pytest`.
