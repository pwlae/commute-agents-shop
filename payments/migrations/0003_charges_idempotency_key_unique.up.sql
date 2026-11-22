ALTER TABLE charges
    ADD CONSTRAINT charges_idempotency_key_key UNIQUE (idempotency_key);
