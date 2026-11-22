package shophttp

import (
	"context"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

type reply struct {
	status int
	delay  time.Duration
}

// stub is a local server that replies from a script and records requests.
type stub struct {
	*httptest.Server
	mu      sync.Mutex
	replies []reply
	seen    []http.Header
}

func newStub(t *testing.T, replies ...reply) *stub {
	s := &stub{replies: replies}
	s.Server = httptest.NewServer(http.HandlerFunc(
		func(w http.ResponseWriter, r *http.Request) {
			s.mu.Lock()
			s.seen = append(s.seen, r.Header.Clone())
			rep := s.replies[0]
			if len(s.replies) > 1 {
				s.replies = s.replies[1:]
			}
			s.mu.Unlock()
			select {
			case <-time.After(rep.delay):
			case <-r.Context().Done():
				return
			}
			w.Header().Set("content-type", "application/json")
			w.WriteHeader(rep.status)
			_, _ = w.Write([]byte(`{"ok":1}`))
		}))
	t.Cleanup(s.Close)
	return s
}

func (s *stub) count() int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return len(s.seen)
}

var ctx = context.Background()

func TestRetries503ThenSucceeds(t *testing.T) {
	s := newStub(t, reply{status: 503}, reply{status: 503},
		reply{status: 200})
	res, err := New(s.URL, Options{BackoffMs: 1}).Get(ctx, "/x")
	if err != nil || res.Status != 200 {
		t.Fatalf("got %v %v", res, err)
	}
	var v map[string]int
	if err := res.JSON(&v); err != nil || v["ok"] != 1 {
		t.Fatalf("json %v %v", v, err)
	}
	if s.count() != 3 {
		t.Fatalf("want 3 requests, got %d", s.count())
	}
}

func TestReturnsLast503WhenRetriesRunOut(t *testing.T) {
	s := newStub(t, reply{status: 503})
	res, err := New(s.URL, Options{BackoffMs: 1}).Get(ctx, "/x")
	if err != nil || res.Status != 503 || s.count() != 3 {
		t.Fatalf("got %v %v, %d requests", res, err, s.count())
	}
}

func TestNoRetryOn400(t *testing.T) {
	s := newStub(t, reply{status: 400})
	c := New(s.URL, Options{BackoffMs: 1})
	res, err := c.Post(ctx, "/x", map[string]int{"a": 1}, "")
	if err != nil || res.Status != 400 || s.count() != 1 {
		t.Fatalf("got %v %v, %d requests", res, err, s.count())
	}
}

func TestNoRetryOn500(t *testing.T) {
	s := newStub(t, reply{status: 500})
	res, err := New(s.URL, Options{BackoffMs: 1}).Get(ctx, "/x")
	if err != nil || res.Status != 500 || s.count() != 1 {
		t.Fatalf("got %v %v, %d requests", res, err, s.count())
	}
}

func TestTimeoutIsNotRetried(t *testing.T) {
	s := newStub(t, reply{status: 200, delay: 300 * time.Millisecond})
	c := New(s.URL, Options{TimeoutMs: 50, BackoffMs: 1})
	_, err := c.Get(ctx, "/slow")
	if !errors.Is(err, ErrTimeout) {
		t.Fatalf("want ErrTimeout, got %v", err)
	}
	if s.count() != 1 {
		t.Fatalf("want 1 request, got %d", s.count())
	}
}

func TestConnectErrorRetriedThenReturned(t *testing.T) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	addr := l.Addr().String()
	l.Close()
	c := New("http://"+addr, Options{BackoffMs: 20})
	t0 := time.Now()
	_, err = c.Get(ctx, "/x")
	if !errors.Is(err, ErrConnect) {
		t.Fatalf("want ErrConnect, got %v", err)
	}
	// two retries: 20 ms + 40 ms of backoff
	if time.Since(t0) < 55*time.Millisecond {
		t.Fatalf("did not back off: %v", time.Since(t0))
	}
}

func TestBreakerOpensAfter5FailuresAndRejectsFast(t *testing.T) {
	s := newStub(t, reply{status: 503})
	c := New(s.URL, Options{Retries: -1,
		Breaker: Breaker{Failures: 5, OpenMs: 60000}})
	for i := 0; i < 5; i++ {
		res, err := c.Get(ctx, "/x")
		if err != nil || res.Status != 503 {
			t.Fatalf("call %d: %v %v", i, res, err)
		}
	}
	if c.State() != "open" {
		t.Fatalf("state %s", c.State())
	}
	t0 := time.Now()
	_, err := c.Get(ctx, "/x")
	if !errors.Is(err, ErrCircuitOpen) {
		t.Fatalf("want ErrCircuitOpen, got %v", err)
	}
	if time.Since(t0) > 20*time.Millisecond {
		t.Fatalf("not fast: %v", time.Since(t0))
	}
	if s.count() != 5 {
		t.Fatalf("want 5 requests, got %d", s.count())
	}
}

func TestBreakerHalfOpensAndClosesOnSuccess(t *testing.T) {
	s := newStub(t, reply{status: 503}, reply{status: 200})
	c := New(s.URL, Options{Retries: -1,
		Breaker: Breaker{Failures: 1, OpenMs: 30}})
	_, _ = c.Get(ctx, "/x")
	if _, err := c.Get(ctx, "/x"); !errors.Is(err, ErrCircuitOpen) {
		t.Fatalf("want ErrCircuitOpen, got %v", err)
	}
	time.Sleep(40 * time.Millisecond)
	res, err := c.Get(ctx, "/x")
	if err != nil || res.Status != 200 || c.State() != "closed" {
		t.Fatalf("got %v %v %s", res, err, c.State())
	}
}

func Test4xxIsNotAFailure(t *testing.T) {
	s := newStub(t, reply{status: 404})
	c := New(s.URL, Options{Breaker: Breaker{Failures: 2}})
	for i := 0; i < 4; i++ {
		_, _ = c.Get(ctx, "/x")
	}
	if c.State() != "closed" {
		t.Fatalf("state %s", c.State())
	}
}

func TestTraceIDAndIdempotencyKeyForwarded(t *testing.T) {
	s := newStub(t, reply{status: 502}, reply{status: 200})
	c := New(s.URL, Options{BackoffMs: 1})
	res, err := c.Post(WithTraceID(ctx, "trace-abc"), "/charges",
		map[string]int{"a": 1}, "order-1")
	if err != nil || res.TraceID != "trace-abc" {
		t.Fatalf("got %v %v", res, err)
	}
	for i, h := range s.seen {
		if h.Get("x-trace-id") != "trace-abc" {
			t.Fatalf("request %d trace %q", i, h.Get("x-trace-id"))
		}
	}
	if s.seen[1].Get("idempotency-key") != "order-1" ||
		s.seen[1].Get("content-type") != "application/json" {
		t.Fatalf("headers %v", s.seen[1])
	}
}

func TestTraceIDGeneratedWhenAbsent(t *testing.T) {
	s := newStub(t, reply{status: 200})
	res, err := New(s.URL, Options{}).Get(ctx, "/x")
	if err != nil || len(res.TraceID) != 36 {
		t.Fatalf("got %v %v", res, err)
	}
	if s.seen[0].Get("x-trace-id") != res.TraceID {
		t.Fatalf("trace not sent")
	}
}
