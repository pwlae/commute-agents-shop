import logging
import os

import psycopg
import shop_http
import uvicorn
from psycopg_pool import ConnectionPool

from . import migrate
from .main import LOG_FORMAT, create_app
from .store import PgStore


def main():
    logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
    dsn = os.environ.get(
        "DATABASE_URL",
        "postgresql://payments:payments@postgres:5432/payments")
    with psycopg.connect(dsn) as conn:
        migrate.up(conn)
    pool = ConnectionPool(dsn, min_size=1, max_size=8, open=True)
    provider = shop_http.client(
        os.environ.get("CARD_PROVIDER_URL", "http://card-provider:8100"),
        timeout_ms=5000)
    orders = shop_http.client(
        os.environ.get("ORDERS_URL", "http://orders:3001"),
        timeout_ms=2000, retries=1)
    app = create_app(PgStore(pool), provider, orders,
                     int(os.environ.get("RECONCILE_EVERY_S", "15")))
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")),
                log_config=None, access_log=False)


if __name__ == "__main__":
    main()
