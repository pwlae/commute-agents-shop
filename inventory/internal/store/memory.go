package store

import (
	"context"
	"fmt"
	"sync"
)

type memStock struct {
	available, reserved, initial int
}

type memRes struct {
	orderID, sku, state string
	qty                 int
}

// Memory is an in-process Store with the same rules as Postgres.
type Memory struct {
	mu    sync.Mutex
	stock map[string]*memStock
	res   []*memRes
	done  map[string]bool
}

// NewMemory starts every SKU at the given quantity.
func NewMemory(seed map[string]int) *Memory {
	m := &Memory{stock: map[string]*memStock{}, done: map[string]bool{}}
	for sku, n := range seed {
		m.stock[sku] = &memStock{available: n, initial: n}
	}
	return m
}

func (m *Memory) once(orderID, cmd string) bool {
	k := orderID + "/" + cmd
	if m.done[k] {
		return false
	}
	m.done[k] = true
	return true
}

func (m *Memory) Reserve(_ context.Context, orderID string, items []Item) (Result, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if !m.once(orderID, CmdReserve) {
		return Result{Duplicate: true}, nil
	}
	if r := validate(items); r != "" {
		return Result{Rejected: true, Reason: r}, nil
	}
	items = merge(items)
	for _, it := range items {
		s, ok := m.stock[it.SKU]
		if !ok {
			return Result{Rejected: true, Reason: "unknown sku " + it.SKU}, nil
		}
		if s.available < it.Qty {
			return Result{Rejected: true, Reason: "out of stock: " + it.SKU}, nil
		}
	}
	for _, it := range items {
		s := m.stock[it.SKU]
		s.available -= it.Qty
		s.reserved += it.Qty
		m.res = append(m.res, &memRes{orderID, it.SKU, "held", it.Qty})
	}
	return Result{Items: items}, nil
}

func (m *Memory) move(orderID, cmd string, items []Item, to string) (Result, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	if !m.once(orderID, cmd) {
		return Result{Duplicate: true}, nil
	}
	want := map[string]bool{}
	for _, it := range items {
		want[it.SKU] = true
	}
	var moved []Item
	for _, r := range m.res {
		if r.orderID != orderID || r.state != "held" {
			continue
		}
		if len(want) > 0 && !want[r.sku] {
			continue
		}
		s := m.stock[r.sku]
		s.reserved -= r.qty
		if to == "released" {
			s.available += r.qty
		}
		r.state = to
		moved = append(moved, Item{SKU: r.sku, Qty: r.qty})
	}
	return Result{Items: merge(moved)}, nil
}

func (m *Memory) Release(_ context.Context, orderID string, items []Item) (Result, error) {
	return m.move(orderID, CmdRelease, items, "released")
}

func (m *Memory) Commit(_ context.Context, orderID string) (Result, error) {
	return m.move(orderID, CmdCommit, nil, "committed")
}

func (m *Memory) Stock(_ context.Context, sku string) (Stock, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	s, ok := m.stock[sku]
	if !ok {
		return Stock{}, fmt.Errorf("%w: %s", ErrUnknownSKU, sku)
	}
	return Stock{SKU: sku, Available: s.available, Reserved: s.reserved}, nil
}

func (m *Memory) Drift(_ context.Context) (int, error) {
	m.mu.Lock()
	defer m.mu.Unlock()
	held, committed := map[string]int{}, map[string]int{}
	for _, r := range m.res {
		switch r.state {
		case "held":
			held[r.sku] += r.qty
		case "committed":
			committed[r.sku] += r.qty
		}
	}
	d := 0
	for sku, s := range m.stock {
		d += abs(s.initial-s.available-s.reserved-committed[sku]) +
			abs(s.reserved-held[sku])
	}
	return d, nil
}

func (m *Memory) Ping(context.Context) error { return nil }

func abs(n int) int {
	if n < 0 {
		return -n
	}
	return n
}
