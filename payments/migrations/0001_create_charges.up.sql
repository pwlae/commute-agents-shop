CREATE TABLE charges (
    id              text PRIMARY KEY,
    order_id        text NOT NULL,
    amount_cents    bigint NOT NULL CHECK (amount_cents > 0),
    idempotency_key text NOT NULL,
    status          text NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
