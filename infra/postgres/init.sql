-- One database per service, each owned by its own user.
-- Local practice passwords only.
CREATE USER orders WITH PASSWORD 'orders';
CREATE DATABASE orders OWNER orders;

CREATE USER payments WITH PASSWORD 'payments';
CREATE DATABASE payments OWNER payments;

CREATE USER inventory WITH PASSWORD 'inventory';
CREATE DATABASE inventory OWNER inventory;

REVOKE ALL ON DATABASE orders FROM PUBLIC;
REVOKE ALL ON DATABASE payments FROM PUBLIC;
REVOKE ALL ON DATABASE inventory FROM PUBLIC;
