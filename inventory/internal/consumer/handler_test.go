package consumer

import (
	"context"
	"encoding/json"
	"io"
	"log"
	"testing"

	"github.com/pwlae/commute-agents-shop/inventory/internal/store"
)

type sent struct {
	topic, key, trace string
	value             []byte
}

type fakePub struct{ msgs []sent }

func (f *fakePub) Publish(_ context.Context, topic, key, trace string, v any) error {
	b, _ := json.Marshal(v)
	f.msgs = append(f.msgs, sent{topic, key, trace, b})
	return nil
}

func setup() (*Handler, *store.Memory, *fakePub) {
	st := store.NewMemory(map[string]int{"MAT-01": 50, "LAMP-02": 50})
	pub := &fakePub{}
	return &Handler{Store: st, Pub: pub, Log: log.New(io.Discard, "", 0)}, st, pub
}

func send(t *testing.T, h *Handler, topic, body string) {
	t.Helper()
	if err := h.Handle(context.Background(), topic, []byte(body), "t-1"); err != nil {
		t.Fatalf("%s: %v", topic, err)
	}
}

func stock(t *testing.T, st store.Store, sku string) store.Stock {
	t.Helper()
	s, err := st.Stock(context.Background(), sku)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

const twoItems = `{"order_id":"o-1","items":[{"sku":"MAT-01","qty":2},{"sku":"LAMP-02","qty":1}]}`

func TestReserveThenReleaseReturnsStock(t *testing.T) {
	h, st, pub := setup()
	send(t, h, TopicReserve, twoItems)
	if s := stock(t, st, "MAT-01"); s.Available != 48 || s.Reserved != 2 {
		t.Fatalf("after reserve: %+v", s)
	}
	if len(pub.msgs) != 1 || pub.msgs[0].topic != TopicReserved ||
		pub.msgs[0].key != "o-1" || pub.msgs[0].trace != "t-1" {
		t.Fatalf("published %+v", pub.msgs)
	}
	send(t, h, TopicRelease, `{"order_id":"o-1"}`)
	for _, sku := range []string{"MAT-01", "LAMP-02"} {
		if s := stock(t, st, sku); s.Available != 50 || s.Reserved != 0 {
			t.Fatalf("after release %s: %+v", sku, s)
		}
	}
	if d, _ := st.Drift(context.Background()); d != 0 {
		t.Fatalf("drift %d", d)
	}
}

func TestReleaseWithItemsReleasesEachListedItem(t *testing.T) {
	h, st, _ := setup()
	send(t, h, TopicReserve, twoItems)
	send(t, h, TopicRelease, twoItems)
	for _, sku := range []string{"MAT-01", "LAMP-02"} {
		if s := stock(t, st, sku); s.Available != 50 || s.Reserved != 0 {
			t.Fatalf("after release %s: %+v", sku, s)
		}
	}
}

func TestReserveTwiceIsNoOp(t *testing.T) {
	h, st, pub := setup()
	send(t, h, TopicReserve, twoItems)
	send(t, h, TopicReserve, twoItems)
	if s := stock(t, st, "MAT-01"); s.Available != 48 || s.Reserved != 2 {
		t.Fatalf("after two reserves: %+v", s)
	}
	if len(pub.msgs) != 1 {
		t.Fatalf("want 1 event, got %d", len(pub.msgs))
	}
}

func TestOutOfStockPublishesRejected(t *testing.T) {
	h, st, pub := setup()
	send(t, h, TopicReserve,
		`{"order_id":"o-2","items":[{"sku":"MAT-01","qty":1},{"sku":"LAMP-02","qty":999}]}`)
	if len(pub.msgs) != 1 || pub.msgs[0].topic != TopicRejected {
		t.Fatalf("published %+v", pub.msgs)
	}
	var rej Rejected
	json.Unmarshal(pub.msgs[0].value, &rej)
	if rej.OrderID != "o-2" || rej.Reason != "out of stock: LAMP-02" {
		t.Fatalf("rejected %+v", rej)
	}
	if s := stock(t, st, "MAT-01"); s.Available != 50 || s.Reserved != 0 {
		t.Fatalf("partial reserve left behind: %+v", s)
	}
}

func TestCommitKeepsStockTaken(t *testing.T) {
	h, st, _ := setup()
	send(t, h, TopicReserve, twoItems)
	send(t, h, TopicCommit, `{"order_id":"o-1"}`)
	send(t, h, TopicRelease, `{"order_id":"o-1"}`)
	if s := stock(t, st, "MAT-01"); s.Available != 48 || s.Reserved != 0 {
		t.Fatalf("after commit: %+v", s)
	}
	if d, _ := st.Drift(context.Background()); d != 0 {
		t.Fatalf("drift %d", d)
	}
}

func TestMissingTraceGetsOne(t *testing.T) {
	h, _, pub := setup()
	h.Handle(context.Background(), TopicReserve, []byte(twoItems), "")
	if len(pub.msgs) != 1 || len(pub.msgs[0].trace) != 16 {
		t.Fatalf("published %+v", pub.msgs)
	}
}
