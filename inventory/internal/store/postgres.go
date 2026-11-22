package store

import (
	"context"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Postgres struct {
	db *pgxpool.Pool
}

func NewPostgres(db *pgxpool.Pool) *Postgres { return &Postgres{db: db} }

func (p *Postgres) Ping(ctx context.Context) error { return p.db.Ping(ctx) }

// claim records the command. false means it was processed before.
func claim(ctx context.Context, tx pgx.Tx, orderID, cmd string) (bool, error) {
	tag, err := tx.Exec(ctx, `
		INSERT INTO processed_commands (order_id, command, result)
		VALUES ($1, $2, 'started')
		ON CONFLICT DO NOTHING`, orderID, cmd)
	if err != nil {
		return false, err
	}
	return tag.RowsAffected() == 1, nil
}

func finish(ctx context.Context, tx pgx.Tx, orderID, cmd, result string) error {
	_, err := tx.Exec(ctx, `
		UPDATE processed_commands SET result = $3
		WHERE order_id = $1 AND command = $2`, orderID, cmd, result)
	return err
}

func (p *Postgres) Reserve(ctx context.Context, orderID string, items []Item) (Result, error) {
	var res Result
	err := pgx.BeginFunc(ctx, p.db, func(tx pgx.Tx) error {
		ok, err := claim(ctx, tx, orderID, CmdReserve)
		if err != nil || !ok {
			res.Duplicate = !ok
			return err
		}
		if r := validate(items); r != "" {
			res = Result{Rejected: true, Reason: r}
			return finish(ctx, tx, orderID, CmdReserve, "rejected: "+r)
		}
		items = merge(items)
		reason, err := p.take(ctx, tx, orderID, items)
		if err != nil {
			return err
		}
		if reason != "" {
			res = Result{Rejected: true, Reason: reason}
			return finish(ctx, tx, orderID, CmdReserve, "rejected: "+reason)
		}
		res = Result{Items: items}
		return finish(ctx, tx, orderID, CmdReserve, "reserved")
	})
	return res, err
}

// take moves stock from available to reserved inside a savepoint.
// It returns a reason when the order cannot be reserved; then
// nothing changes.
func (p *Postgres) take(ctx context.Context, tx pgx.Tx, orderID string, items []Item) (string, error) {
	sp, err := tx.Begin(ctx)
	if err != nil {
		return "", err
	}
	defer sp.Rollback(ctx)
	for _, it := range items {
		tag, err := sp.Exec(ctx, `
			UPDATE stock
			SET available = available - $2,
			    reserved = reserved + $2,
			    updated_at = now()
			WHERE sku = $1 AND available >= $2`, it.SKU, it.Qty)
		if err != nil {
			return "", err
		}
		if tag.RowsAffected() == 0 {
			var one int
			err := sp.QueryRow(ctx,
				`SELECT 1 FROM stock WHERE sku = $1`, it.SKU).Scan(&one)
			if errors.Is(err, pgx.ErrNoRows) {
				return "unknown sku " + it.SKU, nil
			}
			if err != nil {
				return "", err
			}
			return "out of stock: " + it.SKU, nil
		}
		_, err = sp.Exec(ctx, `
			INSERT INTO reservations (order_id, sku, qty)
			VALUES ($1, $2, $3)`, orderID, it.SKU, it.Qty)
		if err != nil {
			return "", err
		}
	}
	return "", sp.Commit(ctx)
}

func (p *Postgres) move(ctx context.Context, orderID, cmd string, items []Item, to string) (Result, error) {
	var res Result
	skus := []string{}
	for _, it := range items {
		skus = append(skus, it.SKU)
	}
	err := pgx.BeginFunc(ctx, p.db, func(tx pgx.Tx) error {
		ok, err := claim(ctx, tx, orderID, cmd)
		if err != nil || !ok {
			res.Duplicate = !ok
			return err
		}
		var moved []Item
		var it Item
		err = tx.QueryRow(ctx, `
			UPDATE reservations
			SET state = $3, updated_at = now()
			WHERE order_id = $1 AND state = 'held'
			  AND (cardinality($2::text[]) = 0 OR sku = ANY($2))
			RETURNING sku, qty`, orderID, skus, to).Scan(&it.SKU, &it.Qty)
		switch {
		case errors.Is(err, pgx.ErrNoRows):
		case err != nil:
			return err
		default:
			moved = append(moved, it)
		}
		back := 0
		if to == "released" {
			back = 1
		}
		for _, it := range merge(moved) {
			_, err := tx.Exec(ctx, `
				UPDATE stock
				SET reserved = reserved - $2,
				    available = available + $2 * $3,
				    updated_at = now()
				WHERE sku = $1`, it.SKU, it.Qty, back)
			if err != nil {
				return err
			}
		}
		res.Items = merge(moved)
		return finish(ctx, tx, orderID, cmd, fmt.Sprintf("%s %d", to, len(moved)))
	})
	return res, err
}

func (p *Postgres) Release(ctx context.Context, orderID string, items []Item) (Result, error) {
	return p.move(ctx, orderID, CmdRelease, items, "released")
}

func (p *Postgres) Commit(ctx context.Context, orderID string) (Result, error) {
	return p.move(ctx, orderID, CmdCommit, nil, "committed")
}

func (p *Postgres) Stock(ctx context.Context, sku string) (Stock, error) {
	s := Stock{SKU: sku}
	err := p.db.QueryRow(ctx,
		`SELECT available, reserved FROM stock WHERE sku = $1`, sku).
		Scan(&s.Available, &s.Reserved)
	if errors.Is(err, pgx.ErrNoRows) {
		return s, fmt.Errorf("%w: %s", ErrUnknownSKU, sku)
	}
	return s, err
}

func (p *Postgres) Drift(ctx context.Context) (int, error) {
	var d int
	err := p.db.QueryRow(ctx, `
		SELECT COALESCE(SUM(
		         abs(s.initial_qty - s.available - s.reserved
		             - COALESCE(r.committed, 0))
		         + abs(s.reserved - COALESCE(r.held, 0))), 0)
		FROM stock s
		LEFT JOIN (
		    SELECT sku,
		           SUM(qty) FILTER (WHERE state = 'held') AS held,
		           SUM(qty) FILTER (WHERE state = 'committed') AS committed
		    FROM reservations GROUP BY sku
		) r ON r.sku = s.sku`).Scan(&d)
	return d, err
}
