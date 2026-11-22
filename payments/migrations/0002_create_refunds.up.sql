CREATE TABLE refunds (
    id         text PRIMARY KEY,
    charge_id  text NOT NULL REFERENCES charges (id),
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX refunds_charge_id_key ON refunds (charge_id);
