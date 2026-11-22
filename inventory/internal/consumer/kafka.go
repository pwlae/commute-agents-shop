package consumer

import (
	"context"
	"encoding/json"
	"log"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
	"github.com/twmb/franz-go/pkg/kadm"
	"github.com/twmb/franz-go/pkg/kgo"
)

const Group = "inventory"

var lagGauge = promauto.NewGauge(prometheus.GaugeOpts{
	Name: "inventory_consumer_lag",
	Help: "Messages on inventory command topics not yet processed.",
})

// Producer publishes events with the trace id as a header.
type Producer struct {
	Client *kgo.Client
}

func (p Producer) Publish(ctx context.Context, topic, key, traceID string, value any) error {
	b, err := json.Marshal(value)
	if err != nil {
		return err
	}
	rec := &kgo.Record{
		Topic: topic,
		Key:   []byte(key),
		Value: b,
		Headers: []kgo.RecordHeader{
			{Key: TraceHeader, Value: []byte(traceID)},
		},
	}
	return p.Client.ProduceSync(ctx, rec).FirstErr()
}

// Run consumes commands until ctx ends. When a message fails, the
// consumer starts again from the last committed offset.
func Run(ctx context.Context, brokers []string, h *Handler) {
	for ctx.Err() == nil {
		err := consume(ctx, brokers, h)
		if ctx.Err() != nil {
			return
		}
		h.Log.Printf("ERROR consumer stopped: %v; restarting in 2s", err)
		select {
		case <-ctx.Done():
		case <-time.After(2 * time.Second):
		}
	}
}

func consume(ctx context.Context, brokers []string, h *Handler) error {
	cl, err := kgo.NewClient(
		kgo.SeedBrokers(brokers...),
		kgo.ConsumerGroup(Group),
		kgo.ConsumeTopics(Topics...),
		kgo.DisableAutoCommit(),
	)
	if err != nil {
		return err
	}
	defer cl.Close()
	for {
		fetches := cl.PollFetches(ctx)
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if err := fetches.Err0(); err != nil {
			return err
		}
		iter := fetches.RecordIter()
		for !iter.Done() {
			r := iter.Next()
			if err := h.Handle(ctx, r.Topic, r.Value, trace(r)); err != nil {
				return err
			}
			if err := cl.CommitRecords(ctx, r); err != nil {
				return err
			}
		}
	}
}

func trace(r *kgo.Record) string {
	for _, hd := range r.Headers {
		if hd.Key == TraceHeader {
			return string(hd.Value)
		}
	}
	return ""
}

// WatchLag updates inventory_consumer_lag every interval.
func WatchLag(ctx context.Context, cl *kgo.Client, every time.Duration, logger *log.Logger) {
	adm := kadm.NewClient(cl)
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		if total, err := lag(ctx, adm); err != nil {
			logger.Printf("WARN cannot read consumer lag: %v", err)
		} else {
			lagGauge.Set(float64(total))
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}

// lag compares the group's committed offsets with the end of each
// partition. It does not depend on the group having members, so a
// consumer that keeps restarting still shows its backlog.
func lag(ctx context.Context, adm *kadm.Client) (int64, error) {
	commits, err := adm.FetchOffsets(ctx, Group)
	if err != nil {
		return 0, err
	}
	if err := commits.Error(); err != nil {
		return 0, err
	}
	starts, err := adm.ListStartOffsets(ctx, Topics...)
	if err != nil {
		return 0, err
	}
	ends, err := adm.ListEndOffsets(ctx, Topics...)
	if err != nil {
		return 0, err
	}
	var total int64
	var perr error
	ends.Each(func(end kadm.ListedOffset) {
		if end.Err != nil {
			perr = end.Err
			return
		}
		var from int64
		if s, ok := starts.Lookup(end.Topic, end.Partition); ok && s.Err == nil {
			from = s.Offset
		}
		if c, ok := commits.Lookup(end.Topic, end.Partition); ok && c.At >= 0 {
			from = c.At
		}
		if end.Offset > from {
			total += end.Offset - from
		}
	})
	return total, perr
}
