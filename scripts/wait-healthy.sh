#!/usr/bin/env bash
# Waits until every container of the stack is ready: services with a
# healthcheck are healthy, others are running, one-shot jobs exited 0.
# Usage: scripts/wait-healthy.sh [timeout_seconds]   (default 180)
set -euo pipefail
cd "$(dirname "$0")/.."
TIMEOUT=${1:-180}
deadline=$(( $(date +%s) + TIMEOUT ))

check() {
  docker compose ps -a --format json | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
rows = json.loads(raw) if raw.startswith("[") else \
    [json.loads(l) for l in raw.splitlines() if l.strip()]
bad = []
for r in rows:
    name, state = r["Service"], r["State"]
    health, code = r.get("Health", ""), r.get("ExitCode", 0)
    if state == "exited" and code == 0:
        continue
    if state == "exited":
        print(f"FAILED {name} exited {code}")
        sys.exit(2)
    if state != "running" or health not in ("", "healthy"):
        bad.append(f"{name}={health or state}")
if not rows:
    bad.append("no containers")
if bad:
    print("waiting: " + " ".join(bad))
    sys.exit(1)
print(f"all {len(rows)} containers ready")
'
}

while true; do
  set +e
  out=$(check)
  rc=$?
  set -e
  if [ $rc -eq 0 ]; then
    echo "$out"
    exit 0
  fi
  if [ $rc -eq 2 ]; then
    echo "$out" >&2
    exit 1
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "timed out after ${TIMEOUT}s: $out" >&2
    docker compose ps -a >&2
    exit 1
  fi
  sleep 2
done
