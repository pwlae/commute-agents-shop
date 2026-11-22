CREATE TABLE processed_commands (
    order_id     text NOT NULL,
    command      text NOT NULL,
    result       text NOT NULL,
    processed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (order_id, command)
);
