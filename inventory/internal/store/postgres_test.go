package store_test

import (
	"context"
	"os"
	"testing"

	"github.com/golang-migrate/migrate/v4"
	_ "github.com/golang-migrate/migrate/v4/database/pgx/v5"
	"github.com/golang-migrate/migrate/v4/source/iofs"
	"github.com/jackc/pgx/v5/pgxpool"

	"github.com/pwlae/commute-agents-shop/inventory/internal/store"
	"github.com/pwlae/commute-agents-shop/inventory/migrations"
)

// Runs only with INVENTORY_TEST_DB=pgx5://user:pass@host:port/db
// pointing at an empty database. It migrates down to zero first.
func pg(t *testing.T) *store.Postgres {
	u := os.Getenv("INVENTORY_TEST_DB")
	if u == "" {
		t.Skip("INVENTORY_TEST_DB not set")
	}
	src, err := iofs.New(migrations.FS, ".")
	if err != nil {
		t.Fatal(err)
	}
	m, err := migrate.NewWithSourceInstance("iofs", src, u)
	if err != nil {
		t.Fatal(err)
	}
	if err := m.Down(); err != nil && err != migrate.ErrNoChange {
		t.Fatal(err)
	}
	if err := m.Up(); err != nil {
		t.Fatal(err)
	}
	db, err := pgxpool.New(context.Background(), "postgres"+u[len("pgx5"):])
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(db.Close)
	return store.NewPostgres(db)
}

func TestPostgresStore(t *testing.T) {
	st := pg(t)
	ctx := context.Background()
	check := func(sku string, avail, res int) {
		t.Helper()
		s, err := st.Stock(ctx, sku)
		if err != nil || s.Available != avail || s.Reserved != res {
			t.Fatalf("%s: %+v %v", sku, s, err)
		}
	}
	two := []store.Item{{"MAT-01", 2}, {"LAMP-02", 1}, {"MAT-01", 1}}

	r, err := st.Reserve(ctx, "o-1", two)
	if err != nil || r.Rejected || r.Duplicate {
		t.Fatalf("reserve %+v %v", r, err)
	}
	check("MAT-01", 47, 3)
	if r, _ := st.Reserve(ctx, "o-1", two); !r.Duplicate {
		t.Fatalf("second reserve %+v", r)
	}
	check("MAT-01", 47, 3)

	r, _ = st.Reserve(ctx, "o-2", []store.Item{{"MUG-05", 1}, {"CHAIR-03", 51}})
	if !r.Rejected || r.Reason != "out of stock: CHAIR-03" {
		t.Fatalf("o-2 %+v", r)
	}
	check("MUG-05", 50, 0)
	r, _ = st.Reserve(ctx, "o-3", []store.Item{{"NOPE", 1}})
	if !r.Rejected || r.Reason != "unknown sku NOPE" {
		t.Fatalf("o-3 %+v", r)
	}

	if r, err := st.Release(ctx, "o-1", nil); err != nil || len(r.Items) != 2 {
		t.Fatalf("release %+v %v", r, err)
	}
	check("MAT-01", 50, 0)
	check("LAMP-02", 50, 0)

	st.Reserve(ctx, "o-4", []store.Item{{"CABLE-04", 4}})
	st.Commit(ctx, "o-4")
	check("CABLE-04", 46, 0)
	if d, err := st.Drift(ctx); err != nil || d != 0 {
		t.Fatalf("drift %d %v", d, err)
	}
	if _, err := st.Stock(ctx, "NOPE"); err == nil {
		t.Fatal("want unknown sku error")
	}
}
