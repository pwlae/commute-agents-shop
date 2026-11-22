DROP INDEX orders_status_created_at_idx;

ALTER TABLE orders
    DROP COLUMN charge_attempts,
    DROP COLUMN charge_id;
