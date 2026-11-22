// Package api serves stock levels, health and metrics over HTTP.
package api

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"strconv"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/pwlae/commute-agents-shop/inventory/internal/consumer"
	"github.com/pwlae/commute-agents-shop/inventory/internal/store"
)

const service = "inventory"

var (
	requests = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "http_requests_total",
		Help: "HTTP requests by route and status code.",
	}, []string{"service", "route", "code"})

	duration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "http_request_duration_seconds",
		Help:    "HTTP request duration.",
		Buckets: prometheus.DefBuckets,
	}, []string{"service", "route"})

	drift = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "inventory_stock_drift",
		Help: "Units by which stock disagrees with the seed and reservations.",
	})
)

type Server struct {
	Store store.Store
	Log   *log.Logger
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /stock/{sku}", s.stock)
	mux.HandleFunc("GET /healthz", s.healthz)
	mux.Handle("GET /metrics", promhttp.Handler())
	return instrument(mux)
}

func (s *Server) stock(w http.ResponseWriter, r *http.Request) {
	trace := r.Header.Get(consumer.TraceHeader)
	if trace == "" {
		trace = consumer.NewTraceID()
	}
	w.Header().Set(consumer.TraceHeader, trace)
	sku := r.PathValue("sku")
	st, err := s.Store.Stock(r.Context(), sku)
	switch {
	case errors.Is(err, store.ErrUnknownSKU):
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "unknown sku"})
	case err != nil:
		s.Log.Printf("ERROR stock lookup for %s failed: %v trace=%s", sku, err, trace)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": "internal error"})
	default:
		writeJSON(w, http.StatusOK, st)
	}
}

func (s *Server) healthz(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := s.Store.Ping(ctx); err != nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"status": "db down"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// WatchDrift updates inventory_stock_drift every interval.
func (s *Server) WatchDrift(ctx context.Context, every time.Duration) {
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		if d, err := s.Store.Drift(ctx); err != nil {
			s.Log.Printf("WARN cannot compute stock drift: %v", err)
		} else {
			drift.Set(float64(d))
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	json.NewEncoder(w).Encode(v)
}

type recorder struct {
	http.ResponseWriter
	code int
}

func (r *recorder) WriteHeader(code int) {
	r.code = code
	r.ResponseWriter.WriteHeader(code)
}

func instrument(mux *http.ServeMux) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		rec := &recorder{ResponseWriter: w, code: http.StatusOK}
		mux.ServeHTTP(rec, r)
		route := r.Pattern
		if route == "" {
			route = "unmatched"
		} else if len(route) > 4 && route[:4] == "GET " {
			route = route[4:]
		}
		if route == "/metrics" || route == "/healthz" {
			return
		}
		requests.WithLabelValues(service, route, strconv.Itoa(rec.code)).Inc()
		duration.WithLabelValues(service, route).Observe(time.Since(start).Seconds())
	})
}
