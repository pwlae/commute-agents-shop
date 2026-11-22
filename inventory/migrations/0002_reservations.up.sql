CREATE TABLE reservations (
    id         bigserial PRIMARY KEY,
    order_id   text NOT NULL,
    sku        text NOT NULL REFERENCES stock (sku),
    qty        integer NOT NULL CHECK (qty > 0),
    state      text NOT NULL DEFAULT 'held'
               CHECK (state IN ('held', 'released', 'committed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
