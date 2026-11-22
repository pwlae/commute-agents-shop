// Package migrations holds the inventory schema, applied at start.
package migrations

import "embed"

//go:embed *.sql
var FS embed.FS
