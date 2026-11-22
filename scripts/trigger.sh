#!/usr/bin/env bash
# Reproduces one planted bug, then waits up to 2 minutes for the
# alert or signal it causes. Maintainer notes: PLANTED_BUGS.md.
# Usage: scripts/trigger.sh <1-6> [--no-wait]
# Run on a fresh, quiet stack (no traffic profile):
#   docker compose down -v && docker compose up -d --build \
#     && scripts/wait-healthy.sh
# Several triggers leave the stack broken on purpose; start a
# fresh stack before the next one.
set -euo pipefail
cd "$(dirname "$0")/.."
GW=${GATEWAY_URL:-http://localhost:8080}
PROM=${PROMETHEUS_URL:-http://localhost:9090}
KEY=${API_KEY:-dev-key}
LIMIT=120

usage() { echo "usage: $0 <1-6> [--no-wait]" >&2; exit 2; }
[ $# -ge 1 ] || usage
bug=$1
wait=1
[ "${2:-}" = --no-wait ] && wait=0

say() { echo "trigger $bug: $*"; }

# place <token> <sku:qty>... -> prints the order id
place() {
  local token=$1; shift
  python3 - "$GW" "$KEY" "$token" "$@" <<'PY'
import json, sys, urllib.request
gw, key, token, *items = sys.argv[1:]
body = {"customer_id": "trigger", "card_token": token,
        "items": [{"sku": s, "qty": int(q)}
                  for s, q in (i.split(":") for i in items)]}
req = urllib.request.Request(
    gw + "/orders", json.dumps(body).encode(),
    {"x-api-key": key, "content-type": "application/json"})
with urllib.request.urlopen(req, timeout=10) as r:
    print(json.load(r)["order_id"])
PY
}

order() {  # order_id -> JSON
  curl -sS -f "$GW/orders/$1" -H "X-Api-Key: $KEY"
}

field() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

wait_status() {  # order_id status
  local got="" i
  for i in $(seq 60); do
    got=$(order "$1" | field '["status"]')
    [ "$got" = "$2" ] && return 0
    [ "$got" != PENDING ] && break
    sleep 0.5
  done
  echo "order $1 is $got, want $2" >&2
  return 1
}

chaos() {  # JSON body for the card provider's chaos switch
  docker compose exec -T card-provider python -c "
import sys, urllib.request
urllib.request.urlopen(urllib.request.Request(
    'http://localhost:8100/admin/chaos', sys.argv[1].encode(),
    {'content-type': 'application/json'}), timeout=5)" "$1"
}

charges() {  # order_id -> JSON list of charges
  docker compose exec -T payments python -c "
import sys, urllib.request
print(urllib.request.urlopen(
    'http://localhost:8000/charges?order_id=' + sys.argv[1],
    timeout=5).read().decode())" "$1"
}

promq() {  # PromQL -> first value, or empty
  curl -sS -G "$PROM/api/v1/query" --data-urlencode "query=$1" \
    | python3 -c '
import json, sys
r = json.load(sys.stdin)["data"]["result"]
print(r[0]["value"][1] if r else "")'
}

firing() {  # alert name -> 0 if firing
  curl -sS "$PROM/api/v1/alerts" | python3 -c '
import json, sys
name = sys.argv[1]
a = json.load(sys.stdin)["data"]["alerts"]
sys.exit(0 if any(x["labels"]["alertname"] == name
                  and x["state"] == "firing" for x in a) else 1)' "$1"
}

# expect <description> <command...>: polls the command every 5 s
expect() {
  local what=$1; shift
  [ "$wait" = 1 ] || { say "not waiting for $what"; return 0; }
  local start now
  start=$(date +%s)
  while true; do
    now=$(date +%s)
    if "$@"; then
      say "PASS: $what after $((now - start))s"
      return 0
    fi
    if [ $((now - start)) -ge "$LIMIT" ]; then
      say "FAIL: no $what within ${LIMIT}s" >&2
      return 1
    fi
    sleep 5
  done
}

REJECTED='sum(orders_saga_transitions_total{from="PENDING",to="REJECTED"})'
rejected_before=0
rejected_spike() {  # at least 5 more REJECTED than before the deploy
  local v
  v=$(promq "$REJECTED")
  [ -n "$v" ] && python3 -c "import sys; sys.exit(0 if float('$v') - float('$rejected_before') >= 5 else 1)"
}

case $bug in
  1)  # orders dies while a charge is in flight; Kafka redelivers
    chaos '{"latency_ms": 1500}'
    id=$(place tok_ok CABLE-04:1)
    say "order $id placed, waiting for its charge to start"
    for i in $(seq 50); do
      [ "$(charges "$id")" != "[]" ] && break
      sleep 0.1
    done
    docker compose kill orders >/dev/null 2>&1
    say "orders killed mid-charge"
    sleep 3
    chaos '{}'
    docker compose start orders >/dev/null 2>&1
    say "orders started again; charges for $id will follow"
    expect "alert DuplicateCharge" firing DuplicateCharge
    if [ "$wait" = 1 ]; then say "charges: $(charges "$id")"; fi
    ;;
  2)  # a three-item order with a declined card
    id=$(place tok_decline MAT-01:1 LAMP-02:1 MUG-05:1)
    wait_status "$id" CANCELLED
    say "order $id CANCELLED"
    expect "alert StockDrift" firing StockDrift
    if [ "$wait" = 1 ]; then say "inventory_stock_drift = $(promq inventory_stock_drift)"; fi
    ;;
  3)  # the card provider answers in 3 s; orders waits 2 s
    chaos '{"latency_ms": 3000}'
    id=$(place tok_ok LAMP-02:1)
    wait_status "$id" CANCELLED
    say "order $id CANCELLED after a slow charge"
    sleep 4
    chaos '{}'
    expect "alert CapturedForFailedOrder" firing CapturedForFailedOrder
    if [ "$wait" = 1 ]; then say "charges: $(charges "$id")"; fi
    ;;
  4)  # one value that is not JSON, then normal orders behind it
    echo not-json | docker compose exec -T kafka \
      /opt/kafka/bin/kafka-console-producer.sh \
      --bootstrap-server localhost:9092 --topic inventory.reserve.v1 \
      >/dev/null 2>&1
    say "not-json produced to inventory.reserve.v1"
    for i in $(seq 30); do
      place tok_decline CABLE-04:1 >/dev/null
    done
    say "30 orders placed behind it"
    expect "alert InventoryConsumerLag" firing InventoryConsumerLag
    if [ "$wait" = 1 ]; then say "inventory_consumer_lag = $(promq inventory_consumer_lag)"; fi
    ;;
  5)  # two desk mats at 9.99
    id=$(place tok_ok MAT-01:2)
    wait_status "$id" CONFIRMED
    total=$(order "$id" | field '["total"]')
    cents=$(charges "$id" | field '[0]["amount_cents"]')
    say "order $id total $total, charged $cents cents"
    if [ "$wait" = 1 ]; then
      if python3 -c "import sys; sys.exit(0 if round(float('$total')*100) != int('$cents') else 1)"; then
        say "PASS: total and charge disagree"
      else
        say "FAIL: total and charge agree" >&2
        exit 1
      fi
    fi
    ;;
  6)  # a bad inventory deploy, then ordinary orders
    rejected_before=$(promq "$REJECTED")
    rejected_before=${rejected_before:-0}
    scripts/deploy.sh inventory --bad
    for sku in MAT-01 LAMP-02 CHAIR-03 CABLE-04 MUG-05 \
               MAT-01 LAMP-02 CHAIR-03 CABLE-04 MUG-05; do
      place tok_ok "$sku:1" >/dev/null
    done
    say "10 orders placed; roll back with scripts/deploy.sh inventory"
    expect "REJECTED spike (orders_saga_transitions_total)" rejected_spike
    if [ "$wait" = 1 ]; then say "PENDING->REJECTED total = $(promq "$REJECTED") (was $rejected_before)"; fi
    ;;
  *) usage ;;
esac
