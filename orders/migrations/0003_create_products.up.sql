CREATE TABLE products (
    sku         text PRIMARY KEY,
    name        text NOT NULL,
    unit_price  real NOT NULL
);

INSERT INTO products (sku, name, unit_price) VALUES
    ('MAT-01',   'Desk mat',     9.99),
    ('LAMP-02',  'Desk lamp',   34.50),
    ('CHAIR-03', 'Office chair', 189.00),
    ('CABLE-04', 'USB-C cable',  4.25),
    ('MUG-05',   'Mug',         12.00);
