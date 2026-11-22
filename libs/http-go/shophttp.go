// Package shophttp is the shared HTTP client: timeouts, retries,
// circuit breaker and x-trace-id forwarding.
package shophttp

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"
)

const (
	TraceHeader       = "x-trace-id"
	IdempotencyHeader = "idempotency-key"
)

var (
	ErrCircuitOpen = errors.New("circuit open")
	ErrTimeout     = errors.New("timeout")
	ErrConnect     = errors.New("connect failed")
)

// Breaker configures the circuit breaker.
type Breaker struct {
	Failures int
	OpenMs   int
}

// Options mirror the TypeScript and Python clients.
// Zero values take the defaults; set Retries to -1 for no retries.
type Options struct {
	TimeoutMs int
	Retries   int
	BackoffMs int
	Breaker   Breaker
}

type Response struct {
	Status  int
	Header  http.Header
	Body    []byte
	TraceID string
}

func (r *Response) JSON(v any) error { return json.Unmarshal(r.Body, v) }

type Client struct {
	BaseURL string
	opts    Options
	http    *http.Client

	mu       sync.Mutex
	failures int
	openedAt time.Time
}

type ctxKey struct{}

// WithTraceID returns a context whose calls forward this trace id.
func WithTraceID(ctx context.Context, id string) context.Context {
	return context.WithValue(ctx, ctxKey{}, id)
}

// TraceID returns the trace id stored in ctx, or "".
func TraceID(ctx context.Context) string {
	id, _ := ctx.Value(ctxKey{}).(string)
	return id
}

func New(baseURL string, opts Options) *Client {
	if opts.TimeoutMs == 0 {
		opts.TimeoutMs = 2000
	}
	if opts.Retries == 0 {
		opts.Retries = 2
	} else if opts.Retries < 0 {
		opts.Retries = 0
	}
	if opts.BackoffMs == 0 {
		opts.BackoffMs = 100
	}
	if opts.Breaker.Failures == 0 {
		opts.Breaker.Failures = 5
	}
	if opts.Breaker.OpenMs == 0 {
		opts.Breaker.OpenMs = 10000
	}
	return &Client{
		BaseURL: strings.TrimRight(baseURL, "/"),
		opts:    opts,
		http:    &http.Client{},
	}
}

// State is "closed", "open" or "half-open".
func (c *Client) State() string {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.state()
}

func (c *Client) state() string {
	if c.openedAt.IsZero() {
		return "closed"
	}
	open := time.Duration(c.opts.Breaker.OpenMs) * time.Millisecond
	if time.Since(c.openedAt) < open {
		return "open"
	}
	return "half-open"
}

func (c *Client) Get(ctx context.Context, path string) (*Response, error) {
	return c.do(ctx, http.MethodGet, path, nil, "")
}

// Post sends body as JSON. idempotencyKey may be "".
func (c *Client) Post(ctx context.Context, path string, body any,
	idempotencyKey string) (*Response, error) {
	var data []byte
	if body != nil {
		var err error
		if data, err = json.Marshal(body); err != nil {
			return nil, err
		}
	}
	return c.do(ctx, http.MethodPost, path, data, idempotencyKey)
}

func newTraceID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	b[6] = b[6]&0x0f | 0x40
	b[8] = b[8]&0x3f | 0x80
	return fmt.Sprintf("%x-%x-%x-%x-%x", b[0:4], b[4:6], b[6:8],
		b[8:10], b[10:])
}

func (c *Client) do(ctx context.Context, method, path string,
	data []byte, key string) (*Response, error) {
	if c.State() == "open" {
		return nil, fmt.Errorf("%w for %s", ErrCircuitOpen, c.BaseURL)
	}
	trace := TraceID(ctx)
	if trace == "" {
		trace = newTraceID()
	}
	url := c.BaseURL + path
	timeout := time.Duration(c.opts.TimeoutMs) * time.Millisecond

	for attempt := 0; ; attempt++ {
		last := attempt >= c.opts.Retries
		res, err := c.once(ctx, method, url, data, key, trace, timeout)
		if err != nil {
			if isTimeout(err) {
				// Not retried: the server may still be doing the work.
				c.onFailure()
				return nil, fmt.Errorf("%w after %v: %s",
					ErrTimeout, timeout, url)
			}
			if isConnect(err) {
				if !last {
					c.sleep(ctx, attempt)
					continue
				}
				c.onFailure()
				return nil, fmt.Errorf("%w: %s: %v", ErrConnect, url, err)
			}
			c.onFailure()
			return nil, err
		}
		switch res.Status {
		case 502, 503, 504:
			if !last {
				c.sleep(ctx, attempt)
				continue
			}
		}
		if res.Status >= 500 {
			c.onFailure()
		} else {
			c.onSuccess()
		}
		return res, nil
	}
}

func (c *Client) once(ctx context.Context, method, url string,
	data []byte, key, trace string,
	timeout time.Duration) (*Response, error) {
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	var body io.Reader
	if data != nil {
		body = bytes.NewReader(data)
	}
	req, err := http.NewRequestWithContext(ctx, method, url, body)
	if err != nil {
		return nil, err
	}
	req.Header.Set(TraceHeader, trace)
	if data != nil {
		req.Header.Set("content-type", "application/json")
	}
	if key != "" {
		req.Header.Set(IdempotencyHeader, key)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	b, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	return &Response{resp.StatusCode, resp.Header, b, trace}, nil
}

func isTimeout(err error) bool {
	if errors.Is(err, context.DeadlineExceeded) {
		return true
	}
	var ne net.Error
	return errors.As(err, &ne) && ne.Timeout()
}

func isConnect(err error) bool {
	var op *net.OpError
	if errors.As(err, &op) && op.Op == "dial" {
		return true
	}
	var dns *net.DNSError
	return errors.As(err, &dns)
}

func (c *Client) sleep(ctx context.Context, attempt int) {
	d := time.Duration(c.opts.BackoffMs<<attempt) * time.Millisecond
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
	case <-ctx.Done():
	}
}

func (c *Client) onSuccess() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.failures = 0
	c.openedAt = time.Time{}
}

func (c *Client) onFailure() {
	c.mu.Lock()
	defer c.mu.Unlock()
	c.failures++
	if c.state() == "half-open" || c.failures >= c.opts.Breaker.Failures {
		c.openedAt = time.Now()
	}
}
