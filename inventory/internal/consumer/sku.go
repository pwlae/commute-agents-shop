package consumer

import (
	"strings"

	"github.com/pwlae/commute-agents-shop/inventory/internal/store"
)

// skuCase is set at build time with
// -ldflags "-X .../internal/consumer.skuCase=lower" for catalogues
// that store SKUs in lower case. Empty keeps SKUs as sent.
var skuCase string

func normalizeSKU(sku string) string {
	sku = strings.TrimSpace(sku)
	if skuCase == "lower" {
		return strings.ToLower(sku)
	}
	return sku
}

func normalizeItems(items []store.Item) []store.Item {
	out := make([]store.Item, len(items))
	for i, it := range items {
		out[i] = store.Item{SKU: normalizeSKU(it.SKU), Qty: it.Qty}
	}
	return out
}
