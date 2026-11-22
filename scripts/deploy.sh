#!/usr/bin/env bash
# Rebuilds and restarts one service, then records the deploy in
# ops/deploys.jsonl as {"service", "git_sha", "time", "note"}.
# Usage: scripts/deploy.sh <service> [--bad] [--note "text"]
#   --bad   inventory only: build with BAD_SKU_CASE=1
#           (maintainer notes: PLANTED_BUGS.md, bug 6)
# Deploying the same service again without --bad rolls it back.
set -euo pipefail
cd "$(dirname "$0")/.."

usage() { echo "usage: $0 <service> [--bad] [--note text]" >&2; exit 2; }
[ $# -ge 1 ] || usage
service=$1; shift
bad=0
note=""
while [ $# -gt 0 ]; do
  case $1 in
    --bad) bad=1 ;;
    --note) [ $# -ge 2 ] || usage; note=$2; shift ;;
    *) usage ;;
  esac
  shift
done

services=$(docker compose config --services)
if ! grep -qx "$service" <<<"$services"; then
  echo "unknown service: $service" >&2
  exit 2
fi
if [ "$bad" = 1 ] && [ "$service" != inventory ]; then
  echo "--bad only exists for inventory" >&2
  exit 2
fi

sha=$(git rev-parse --short=7 HEAD 2>/dev/null || echo unknown)
if [ -z "$note" ]; then
  if [ "$bad" = 1 ]; then
    note="inventory: normalise SKU case on lookup"
  else
    note=$(git log -1 --format=%s 2>/dev/null || echo "")
  fi
fi

BAD_SKU_CASE=$bad docker compose up -d --build --no-deps --wait "$service"

mkdir -p ops
python3 - "$service" "$sha" "$note" >> ops/deploys.jsonl <<'PY'
import json, sys
from datetime import datetime, timezone
service, sha, note = sys.argv[1:4]
now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
print(json.dumps({"service": service, "git_sha": sha,
                  "time": now, "note": note}))
PY
echo "deployed $service $sha: $note"
tail -n 1 ops/deploys.jsonl
