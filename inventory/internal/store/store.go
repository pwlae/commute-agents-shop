// Package store keeps stock levels and reservations.
package store

import (
	"context"
	"errors"
	"sort"
)

// Command names, also the second half of the idempotency key.
const (
	CmdReserve = "reserve"
	CmdRelease = "release"
	CmdCommit  = "commit"
)

var ErrUnknownSKU = errors.New("unknown sku")

type Item struct {
	SKU string `json:"sku"`
	Qty int    `json:"qty"`
}

type Stock struct {
	SKU       string `json:"sku"`
	Available int    `json:"available"`
	Reserved  int    `json:"reserved"`
}

// Result says what a command did. Duplicate means the command
// was already processed for this order and nothing changed.
type Result struct {
	Duplicate bool
	Rejected  bool
	Reason    string
	Items     []Item
}

type Store interface {
	Reserve(ctx context.Context, orderID string, items []Item) (Result, error)
	// Release gives back held stock. With no items it releases
	// everything the order holds.
	Release(ctx context.Context, orderID string, items []Item) (Result, error)
	Commit(ctx context.Context, orderID string) (Result, error)
	Stock(ctx context.Context, sku string) (Stock, error)
	// Drift is how far the stock table is from what the seed and
	// the reservations say it should be. 0 when consistent.
	Drift(ctx context.Context) (int, error)
	Ping(ctx context.Context) error
}

// merge adds up quantities per SKU and sorts by SKU, so rows are
// always locked in the same order.
func merge(items []Item) []Item {
	sum := map[string]int{}
	for _, it := range items {
		sum[it.SKU] += it.Qty
	}
	out := make([]Item, 0, len(sum))
	for sku, qty := range sum {
		out = append(out, Item{SKU: sku, Qty: qty})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].SKU < out[j].SKU })
	return out
}

func validate(items []Item) string {
	if len(items) == 0 {
		return "no items"
	}
	for _, it := range items {
		if it.SKU == "" || it.Qty <= 0 {
			return "invalid item"
		}
	}
	return ""
}
