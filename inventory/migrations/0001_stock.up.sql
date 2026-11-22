CREATE TABLE stock (
    sku         text PRIMARY KEY,
    name        text NOT NULL,
    available   integer NOT NULL CHECK (available >= 0),
    reserved    integer NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    initial_qty integer NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);
