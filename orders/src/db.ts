import pg from "pg";

export type Status = "PENDING" | "CONFIRMED" | "CANCELLED" | "REJECTED";

export interface ItemInput {
  sku: string;
  qty: number;
}

export interface Item extends ItemInput {
  unitPrice: number;
}

export interface Order {
  id: string;
  customerId: string;
  status: Status;
  cardToken: string;
  items: Item[];
  chargeId: string | null;
  createdAt: Date;
}

export interface NewOrder {
  id: string;
  customerId: string;
  cardToken: string;
  items: ItemInput[];
}

export type CreateResult =
  | { ok: true; order: Order }
  | { ok: false; unknownSkus: string[] };

export interface Store {
  createOrder(o: NewOrder): Promise<CreateResult>;
  getOrder(id: string): Promise<Order | null>;
  // Moves the order only if it is still in `from`. False otherwise.
  setStatus(
    id: string,
    from: Status,
    to: Status,
    chargeId?: string,
  ): Promise<boolean>;
  nextChargeAttempt(id: string): Promise<number>;
  pendingOldestSeconds(): Promise<number>;
  ping(): Promise<void>;
}

export class PgStore implements Store {
  constructor(private pool: pg.Pool) {}

  static connect(url: string): PgStore {
    return new PgStore(new pg.Pool({ connectionString: url, max: 5 }));
  }

  async createOrder(o: NewOrder): Promise<CreateResult> {
    const skus = [...new Set(o.items.map((i) => i.sku))];
    const c = await this.pool.connect();
    try {
      await c.query("BEGIN");
      const known = await c.query<{ sku: string }>(
        "SELECT sku FROM products WHERE sku = ANY($1)",
        [skus],
      );
      const found = new Set(known.rows.map((r) => r.sku));
      const unknownSkus = skus.filter((s) => !found.has(s));
      if (unknownSkus.length > 0) {
        await c.query("ROLLBACK");
        return { ok: false, unknownSkus };
      }
      await c.query(
        `INSERT INTO orders (id, customer_id, status, card_token)
         VALUES ($1, $2, 'PENDING', $3)`,
        [o.id, o.customerId, o.cardToken],
      );
      await c.query(
        `INSERT INTO order_items (order_id, sku, qty, unit_price)
         SELECT $1, p.sku, x.qty, p.unit_price
         FROM unnest($2::text[], $3::int[])
              WITH ORDINALITY AS x(sku, qty, n)
         JOIN products p ON p.sku = x.sku
         ORDER BY x.n`,
        [o.id, o.items.map((i) => i.sku), o.items.map((i) => i.qty)],
      );
      await c.query("COMMIT");
    } catch (err) {
      await c.query("ROLLBACK").catch(() => undefined);
      throw err;
    } finally {
      c.release();
    }
    return { ok: true, order: (await this.getOrder(o.id))! };
  }

  async getOrder(id: string): Promise<Order | null> {
    const r = await this.pool.query(
      `SELECT id, customer_id, status, card_token, charge_id, created_at
       FROM orders WHERE id = $1`,
      [id],
    );
    if (r.rowCount === 0) return null;
    const row = r.rows[0];
    const items = await this.pool.query(
      `SELECT sku, qty, unit_price FROM order_items
       WHERE order_id = $1 ORDER BY id`,
      [id],
    );
    return {
      id: row.id,
      customerId: row.customer_id,
      status: row.status,
      cardToken: row.card_token,
      chargeId: row.charge_id,
      createdAt: row.created_at,
      items: items.rows.map((i) => ({
        sku: i.sku,
        qty: i.qty,
        unitPrice: i.unit_price,
      })),
    };
  }

  async setStatus(
    id: string,
    from: Status,
    to: Status,
    chargeId?: string,
  ): Promise<boolean> {
    const r = await this.pool.query(
      `UPDATE orders
       SET status = $3, charge_id = COALESCE($4, charge_id),
           updated_at = now()
       WHERE id = $1 AND status = $2`,
      [id, from, to, chargeId ?? null],
    );
    return r.rowCount === 1;
  }

  async nextChargeAttempt(id: string): Promise<number> {
    const r = await this.pool.query<{ charge_attempts: number }>(
      `UPDATE orders SET charge_attempts = charge_attempts + 1
       WHERE id = $1 RETURNING charge_attempts`,
      [id],
    );
    return r.rows[0]?.charge_attempts ?? 0;
  }

  async pendingOldestSeconds(): Promise<number> {
    const r = await this.pool.query<{ age: number | null }>(
      `SELECT EXTRACT(EPOCH FROM now() - min(created_at))::float8 AS age
       FROM orders WHERE status = 'PENDING'`,
    );
    return r.rows[0]?.age ?? 0;
  }

  async ping(): Promise<void> {
    await this.pool.query("SELECT 1");
  }

  end(): Promise<void> {
    return this.pool.end();
  }
}
