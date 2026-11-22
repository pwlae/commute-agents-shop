// Command inventory runs the inventory service: Kafka consumer for
// stock commands plus a small HTTP API.
package main

import (
	"context"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	"github.com/golang-migrate/migrate/v4"
	_ "github.com/golang-migrate/migrate/v4/database/pgx/v5"
	"github.com/golang-migrate/migrate/v4/source/iofs"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/twmb/franz-go/pkg/kgo"

	"github.com/pwlae/commute-agents-shop/inventory/internal/api"
	"github.com/pwlae/commute-agents-shop/inventory/internal/consumer"
	"github.com/pwlae/commute-agents-shop/inventory/internal/store"
	"github.com/pwlae/commute-agents-shop/inventory/migrations"
)

func env(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func main() {
	port := env("PORT", "8200")
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(healthcheck(port))
	}
	logger := log.New(os.Stdout, "", log.LstdFlags)
	dbURL := env("DATABASE_URL",
		"postgres://inventory:inventory@postgres:5432/inventory?sslmode=disable")
	brokers := strings.Split(env("KAFKA_BROKERS", "kafka:9092"), ",")

	ctx, stop := signal.NotifyContext(context.Background(),
		os.Interrupt, syscall.SIGTERM)
	defer stop()

	if err := migrateUp(dbURL); err != nil {
		logger.Fatalf("ERROR migrations failed: %v", err)
	}
	db, err := pgxpool.New(ctx, dbURL)
	if err != nil {
		logger.Fatalf("ERROR cannot open database: %v", err)
	}
	defer db.Close()
	st := store.NewPostgres(db)

	prod, err := kgo.NewClient(kgo.SeedBrokers(brokers...))
	if err != nil {
		logger.Fatalf("ERROR cannot create kafka client: %v", err)
	}
	defer prod.Close()

	h := &consumer.Handler{Store: st, Pub: consumer.Producer{Client: prod}, Log: logger}
	srv := &api.Server{Store: st, Log: logger}

	go consumer.Run(ctx, brokers, h)
	go consumer.WatchLag(ctx, prod, 5*time.Second, logger)
	go srv.WatchDrift(ctx, 10*time.Second)

	hs := &http.Server{Addr: ":" + port, Handler: srv.Handler(),
		ReadHeaderTimeout: 5 * time.Second}
	go func() {
		<-ctx.Done()
		sctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		hs.Shutdown(sctx)
	}()
	logger.Printf("INFO inventory listening on :%s", port)
	if err := hs.ListenAndServe(); !errors.Is(err, http.ErrServerClosed) {
		logger.Fatalf("ERROR http server: %v", err)
	}
}

func migrateUp(dbURL string) error {
	src, err := iofs.New(migrations.FS, ".")
	if err != nil {
		return err
	}
	u := "pgx5://" + strings.SplitN(dbURL, "://", 2)[1]
	var m *migrate.Migrate
	for i := 0; ; i++ {
		m, err = migrate.NewWithSourceInstance("iofs", src, u)
		if err == nil || i == 15 {
			break
		}
		time.Sleep(time.Second)
	}
	if err != nil {
		return err
	}
	defer m.Close()
	if err := m.Up(); err != nil && !errors.Is(err, migrate.ErrNoChange) {
		return err
	}
	return nil
}

func healthcheck(port string) int {
	c := http.Client{Timeout: 3 * time.Second}
	resp, err := c.Get("http://127.0.0.1:" + port + "/healthz")
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return 1
	}
	return 0
}
