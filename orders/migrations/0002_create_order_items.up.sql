CREATE TABLE order_items (
    id          bigserial PRIMARY KEY,
    order_id    uuid NOT NULL REFERENCES orders (id),
    sku         text NOT NULL,
    qty         integer NOT NULL CHECK (qty > 0),
    unit_price  double precision NOT NULL
);

CREATE INDEX order_items_order_id_idx ON order_items (order_id);
