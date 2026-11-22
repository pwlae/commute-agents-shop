"""Charges and refunds storage: Postgres, plus an in-memory twin for tests."""
import threading
import uuid
from datetime import datetime, timezone

COLUMNS = ("id, order_id, amount_cents, idempotency_key, status,"
           " provider_ref, decline_reason, created_at")


def new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _charge(row):
    keys = [c.strip() for c in COLUMNS.split(",")]
    d = dict(zip(keys, row))
    d["created_at"] = d["created_at"].isoformat()
    return d


class PgStore:
    def __init__(self, pool):
        self.pool = pool

    def ping(self):
        with self.pool.connection() as c:
            c.execute("SELECT 1")

    def create_pending(self, order_id, amount_cents, idempotency_key):
        """Insert a pending charge. Returns (charge, created)."""
        with self.pool.connection() as c:
            row = c.execute(
                "INSERT INTO charges (id, order_id, amount_cents,"
                " idempotency_key, status)"
                " VALUES (%s, %s, %s, %s, 'pending')"
                " ON CONFLICT (idempotency_key) DO NOTHING"
                f" RETURNING {COLUMNS}",
                (new_id("ch"), order_id, amount_cents,
                 idempotency_key)).fetchone()
            if row:
                return _charge(row), True
            row = c.execute(
                f"SELECT {COLUMNS} FROM charges"
                " WHERE idempotency_key = %s",
                (idempotency_key,)).fetchone()
            return _charge(row), False

    def finish(self, charge_id, status, provider_ref, reason):
        with self.pool.connection() as c:
            row = c.execute(
                "UPDATE charges SET status = %s, provider_ref = %s,"
                " decline_reason = %s, updated_at = now()"
                f" WHERE id = %s RETURNING {COLUMNS}",
                (status, provider_ref, reason, charge_id)).fetchone()
            return _charge(row)

    def discard(self, charge_id):
        with self.pool.connection() as c:
            c.execute("DELETE FROM charges WHERE id = %s"
                      " AND status = 'pending'", (charge_id,))

    def get(self, charge_id):
        with self.pool.connection() as c:
            row = c.execute(f"SELECT {COLUMNS} FROM charges"
                            " WHERE id = %s", (charge_id,)).fetchone()
            return _charge(row) if row else None

    def by_order(self, order_id):
        with self.pool.connection() as c:
            rows = c.execute(f"SELECT {COLUMNS} FROM charges"
                             " WHERE order_id = %s ORDER BY created_at",
                             (order_id,)).fetchall()
            return [_charge(r) for r in rows]

    def create_refund(self, charge_id):
        """Returns (refund, created)."""
        with self.pool.connection() as c:
            row = c.execute(
                "INSERT INTO refunds (id, charge_id) VALUES (%s, %s)"
                " ON CONFLICT (charge_id) DO NOTHING"
                " RETURNING id, charge_id, created_at",
                (new_id("re"), charge_id)).fetchone()
            created = row is not None
            if not created:
                row = c.execute(
                    "SELECT id, charge_id, created_at FROM refunds"
                    " WHERE charge_id = %s", (charge_id,)).fetchone()
            return {"id": row[0], "charge_id": row[1],
                    "created_at": row[2].isoformat()}, created

    def approved_unrefunded(self, limit=500):
        with self.pool.connection() as c:
            rows = c.execute(
                "SELECT c.id, c.order_id FROM charges c"
                " LEFT JOIN refunds r ON r.charge_id = c.id"
                " WHERE c.status = 'approved' AND r.id IS NULL"
                "   AND c.created_at > now() - interval '1 day'"
                " ORDER BY c.created_at DESC LIMIT %s",
                (limit,)).fetchall()
            return [(r[0], r[1]) for r in rows]


class MemoryStore:
    """Same behaviour as PgStore, for unit tests."""

    def __init__(self):
        self.charges = {}
        self.refunds = {}
        self.lock = threading.Lock()

    def ping(self):
        pass

    def create_pending(self, order_id, amount_cents, idempotency_key):
        with self.lock:
            for ch in self.charges.values():
                if ch["idempotency_key"] == idempotency_key:
                    return dict(ch), False
            ch = {"id": new_id("ch"), "order_id": order_id,
                  "amount_cents": amount_cents,
                  "idempotency_key": idempotency_key,
                  "status": "pending", "provider_ref": None,
                  "decline_reason": None,
                  "created_at": datetime.now(timezone.utc).isoformat()}
            self.charges[ch["id"]] = ch
            return dict(ch), True

    def finish(self, charge_id, status, provider_ref, reason):
        with self.lock:
            ch = self.charges[charge_id]
            ch.update(status=status, provider_ref=provider_ref,
                      decline_reason=reason)
            return dict(ch)

    def discard(self, charge_id):
        with self.lock:
            if self.charges.get(charge_id, {}).get("status") == "pending":
                del self.charges[charge_id]

    def get(self, charge_id):
        ch = self.charges.get(charge_id)
        return dict(ch) if ch else None

    def by_order(self, order_id):
        return [dict(c) for c in self.charges.values()
                if c["order_id"] == order_id]

    def create_refund(self, charge_id):
        with self.lock:
            if charge_id in self.refunds:
                return dict(self.refunds[charge_id]), False
            r = {"id": new_id("re"), "charge_id": charge_id,
                 "created_at": datetime.now(timezone.utc).isoformat()}
            self.refunds[charge_id] = r
            return dict(r), True

    def approved_unrefunded(self, limit=500):
        return [(c["id"], c["order_id"]) for c in self.charges.values()
                if c["status"] == "approved"
                and c["id"] not in self.refunds][:limit]
