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
		lags, err := adm.Lag(ctx, Group)
		if err == nil {
			err = lags.Error()
		}
		if err != nil {
			logger.Printf("WARN cannot read consumer lag: %v", err)
		} else {
			var total int64
			for _, l := range lags[Group].Lag.Sorted() {
				if l.Lag > 0 {
					total += l.Lag
				}
			}
			lagGauge.Set(float64(total))
		}
		select {
		case <-ctx.Done():
			return
		case <-t.C:
		}
	}
}
