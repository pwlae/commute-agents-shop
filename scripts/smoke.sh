#!/usr/bin/env bash
# End-to-end check of the saga through the gateway. Places three
# orders and waits up to 20 s for each to reach its final status:
#   CABLE-04 x1, tok_ok       -> CONFIRMED
#   LAMP-02 x1,  tok_decline  -> CANCELLED, stock back to before
#   CHAIR-03 x999             -> REJECTED
# Exits non-zero on the first mismatch. Run on a quiet stack
# (no traffic profile), after scripts/wait-healthy.sh.
set -euo pipefail
cd "$(dirname "$0")/.."
GW=${GATEWAY_URL:-http://localhost:8080}
KEY=${API_KEY:-dev-key}
WAIT=20

json() { python3 -c "import json,sys; print(json.load(sys.stdin)$1)"; }

place() {  # sku qty token -> order id
  local body out
  body=$(printf '{"customer_id":"smoke","items":[{"sku":"%s","qty":%s}],"card_token":"%s"}' "$1" "$2" "$3")
  out=$(curl -sS -f -X POST "$GW/orders" -H "X-Api-Key: $KEY" \
    -H 'content-type: application/json' -d "$body")
  [ "$(echo "$out" | json '["status"]')" = PENDING ] \
    || { echo "FAIL: POST /orders gave $out" >&2; exit 1; }
  echo "$out" | json '["order_id"]'
}

status() {
  curl -sS -f "$GW/orders/$1" -H "X-Api-Key: $KEY" | json '["status"]'
}

expect() {  # order_id status
  local got="" i
  for i in $(seq $((WAIT * 2))); do
    got=$(status "$1")
    [ "$got" = "$2" ] && { echo "ok   order $1 $got"; return 0; }
    [ "$got" != PENDING ] && break
    sleep 0.5
  done
  echo "FAIL: order $1 is $got, want $2" >&2
  exit 1
}

stock() {  # sku -> "available reserved"
  docker compose exec -T inventory wget -qO- \
    "http://localhost:8200/stock/$1" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["available"], d["reserved"])'
}

ok_id=$(place CABLE-04 1 tok_ok)
expect "$ok_id" CONFIRMED

before=$(stock LAMP-02)
dec_id=$(place LAMP-02 1 tok_decline)
expect "$dec_id" CANCELLED
after=""
for i in $(seq $((WAIT * 2))); do
  after=$(stock LAMP-02)
  [ "$after" = "$before" ] && break
  sleep 0.5
done
[ "$after" = "$before" ] \
  || { echo "FAIL: LAMP-02 stock $after, want $before" >&2; exit 1; }
echo "ok   LAMP-02 stock back to $before (available reserved)"

rej_id=$(place CHAIR-03 999 tok_ok)
expect "$rej_id" REJECTED

echo "smoke: PASS"
