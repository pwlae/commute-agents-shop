ALTER TABLE orders
    ADD COLUMN charge_id text,
    ADD COLUMN charge_attempts integer NOT NULL DEFAULT 0;

CREATE INDEX orders_status_created_at_idx
    ON orders (status, created_at);
