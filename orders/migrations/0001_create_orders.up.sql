CREATE TABLE orders (
    id          uuid PRIMARY KEY,
    customer    text NOT NULL,
    status      text NOT NULL,
    card_token  text NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    updated_at  timestamptz NOT NULL DEFAULT now()
);
