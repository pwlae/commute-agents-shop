// Package consumer applies inventory commands from Kafka.
package consumer

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"log"

	"github.com/pwlae/commute-agents-shop/inventory/internal/store"
)

const (
	TopicReserve  = "inventory.reserve.v1"
	TopicRelease  = "inventory.release.v1"
	TopicCommit   = "inventory.commit.v1"
	TopicReserved = "inventory.reserved.v1"
	TopicRejected = "inventory.rejected.v1"

	TraceHeader = "trace-id"
)

// Topics the service consumes.
var Topics = []string{TopicReserve, TopicRelease, TopicCommit}

type Command struct {
	OrderID string       `json:"order_id"`
	Items   []store.Item `json:"items,omitempty"`
}

type Rejected struct {
	OrderID string `json:"order_id"`
	Reason  string `json:"reason"`
}

type Publisher interface {
	Publish(ctx context.Context, topic, key, traceID string, value any) error
}

type Handler struct {
	Store store.Store
	Pub   Publisher
	Log   *log.Logger
}

// NewTraceID makes a trace id for messages that arrive without one.
func NewTraceID() string {
	b := make([]byte, 8)
	rand.Read(b)
	return hex.EncodeToString(b)
}

// Handle applies one command. An error means the message was not
// processed and must be delivered again.
func (h *Handler) Handle(ctx context.Context, topic string, value []byte, traceID string) error {
	if traceID == "" {
		traceID = NewTraceID()
	}
	var cmd Command
	if err := json.Unmarshal(value, &cmd); err != nil {
		return fmt.Errorf("decode %s: %w", topic, err)
	}
	if cmd.OrderID == "" {
		return fmt.Errorf("decode %s: missing order_id", topic)
	}
	switch topic {
	case TopicReserve:
		return h.reserve(ctx, cmd, traceID)
	case TopicRelease:
		res, err := h.Store.Release(ctx, cmd.OrderID, cmd.Items)
		if err != nil {
			return err
		}
		h.done("release", cmd.OrderID, res, traceID)
	case TopicCommit:
		res, err := h.Store.Commit(ctx, cmd.OrderID)
		if err != nil {
			return err
		}
		h.done("commit", cmd.OrderID, res, traceID)
	default:
		h.Log.Printf("WARN ignoring message on unexpected topic %s", topic)
	}
	return nil
}

func (h *Handler) reserve(ctx context.Context, cmd Command, traceID string) error {
	res, err := h.Store.Reserve(ctx, cmd.OrderID, cmd.Items)
	if err != nil {
		return err
	}
	switch {
	case res.Duplicate:
		h.Log.Printf("INFO reserve for order %s already processed, skipping trace=%s",
			cmd.OrderID, traceID)
		return nil
	case res.Rejected:
		h.Log.Printf("WARN rejected order %s: %s trace=%s",
			cmd.OrderID, res.Reason, traceID)
		return h.Pub.Publish(ctx, TopicRejected, cmd.OrderID, traceID,
			Rejected{OrderID: cmd.OrderID, Reason: res.Reason})
	}
	h.Log.Printf("INFO reserved %d items for order %s trace=%s",
		count(res.Items), cmd.OrderID, traceID)
	return h.Pub.Publish(ctx, TopicReserved, cmd.OrderID, traceID,
		Command{OrderID: cmd.OrderID, Items: res.Items})
}

func (h *Handler) done(what, orderID string, res store.Result, traceID string) {
	if res.Duplicate {
		h.Log.Printf("INFO %s for order %s already processed, skipping trace=%s",
			what, orderID, traceID)
		return
	}
	h.Log.Printf("INFO %s order %s: %d items trace=%s",
		what, orderID, count(res.Items), traceID)
}

func count(items []store.Item) int {
	n := 0
	for _, it := range items {
		n += it.Qty
	}
	return n
}
