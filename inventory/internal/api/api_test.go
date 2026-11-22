package api

import (
	"io"
	"log"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/pwlae/commute-agents-shop/inventory/internal/store"
)

func server() *httptest.Server {
	s := &Server{Store: store.NewMemory(map[string]int{"MAT-01": 50}),
		Log: log.New(io.Discard, "", 0)}
	return httptest.NewServer(s.Handler())
}

func get(t *testing.T, url string, hdr map[string]string) (*http.Response, string) {
	t.Helper()
	req, _ := http.NewRequest("GET", url, nil)
	for k, v := range hdr {
		req.Header.Set(k, v)
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	b, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	return resp, string(b)
}

func TestStock(t *testing.T) {
	ts := server()
	defer ts.Close()
	resp, body := get(t, ts.URL+"/stock/MAT-01", map[string]string{"trace-id": "abc"})
	if resp.StatusCode != 200 ||
		strings.TrimSpace(body) != `{"sku":"MAT-01","available":50,"reserved":0}` {
		t.Fatalf("%d %s", resp.StatusCode, body)
	}
	if resp.Header.Get("trace-id") != "abc" {
		t.Fatalf("trace-id %q", resp.Header.Get("trace-id"))
	}
	resp, _ = get(t, ts.URL+"/stock/NOPE", nil)
	if resp.StatusCode != 404 || resp.Header.Get("trace-id") == "" {
		t.Fatalf("unknown sku: %d %v", resp.StatusCode, resp.Header)
	}
	resp, _ = get(t, ts.URL+"/healthz", nil)
	if resp.StatusCode != 200 {
		t.Fatalf("healthz %d", resp.StatusCode)
	}
	_, metrics := get(t, ts.URL+"/metrics", nil)
	for _, want := range []string{
		`http_requests_total{code="200",route="/stock/{sku}",service="inventory"} 1`,
		`http_requests_total{code="404",route="/stock/{sku}",service="inventory"} 1`,
		`http_request_duration_seconds_bucket{route="/stock/{sku}",service="inventory"`,
		`inventory_stock_drift`,
	} {
		if !strings.Contains(metrics, want) {
			t.Errorf("metrics missing %s", want)
		}
	}
}
