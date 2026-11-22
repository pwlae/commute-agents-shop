ALTER TABLE charges
    ADD COLUMN provider_ref   text,
    ADD COLUMN decline_reason text;
