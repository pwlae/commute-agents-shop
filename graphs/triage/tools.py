"""Discovery tools for the triage graph.

Each tool reads one source of facts about the running shop:
deploys from ops/deploys.jsonl, logs from Docker Compose,
metrics from Prometheus. `signature` turns a log line into a
stable string so the same error can be found again later.

Standard library only, Python 3.10+.
"""
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEPLOYS = ROOT / "ops" / "deploys.jsonl"
PROMETHEUS = "http://localhost:9090"

LOG_KEEP = re.compile(r"error|ERROR|panic|Traceback|WARN")
# "orders-1  | " added by `docker compose logs`
COMPOSE_PREFIX = re.compile(r"^[\w.-]+\s+\|\s?")


def parse_time(value: str) -> datetime:
    """ISO 8601 to an aware UTC datetime.

    Accepts a trailing "Z" (Python 3.10 does not) and treats
    a time without an offset as UTC.
    """
    s = value.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    t = datetime.fromisoformat(s)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc)


def deploys(services: list[str], since: str, until: str,
            path: Path = DEPLOYS) -> list[dict]:
    """Deploys of `services` with since <= time <= until.

    An empty `services` list means every service. Results are
    sorted by time. A missing file means no deploys.
    """
    lo, hi = parse_time(since), parse_time(until)
    wanted = set(services)
    out = []
    p = Path(path)
    if not p.exists():
        return out
    for line in p.read_text().splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if wanted and d.get("service") not in wanted:
            continue
        if lo <= parse_time(d["time"]) <= hi:
            out.append(d)
    out.sort(key=lambda d: parse_time(d["time"]))
    return out


def logs(service: str, since: str, until: str,
         limit: int = 200) -> list[str]:
    """Error and warning lines of one service in a window.

    Keeps the newest `limit` lines, without the Compose
    "service-1 | " prefix.
    """
    cmd = ["docker", "compose", "logs", "--no-color",
           "--since", since, "--until", until, service]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True,
                       text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(
            f"docker compose logs {service}: {r.stderr.strip()}")
    kept = [COMPOSE_PREFIX.sub("", ln)
            for ln in r.stdout.splitlines() if LOG_KEEP.search(ln)]
    return kept[-limit:] if limit else kept


def metric(query: str, since: str, until: str,
           step: str = "30s") -> list[list]:
    """A PromQL range query.

    Returns one [labels, values] pair per series, where values
    is Prometheus's list of [unix_time, "value"].
    """
    base = os.environ.get("PROMETHEUS_URL", PROMETHEUS)
    qs = urllib.parse.urlencode({
        "query": query,
        "start": parse_time(since).timestamp(),
        "end": parse_time(until).timestamp(),
        "step": step,
    })
    url = base.rstrip("/") + "/api/v1/query_range?" + qs
    with urllib.request.urlopen(url, timeout=30) as r:
        body = json.load(r)
    if body.get("status") != "success":
        raise RuntimeError(f"prometheus: {body.get('error')}")
    return [[s.get("metric", {}), s.get("values", [])]
            for s in body["data"]["result"]]


# Order matters: trace ids and uuids before hex, timestamps
# before plain numbers.
_TRACE = re.compile(
    r"""(["']?(?:x[-_])?trace[-_]?id["']?\s*[:=]\s*["']?)"""
    r"""[\w-]+""", re.I)
_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_TIME = re.compile(
    r"\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?", re.I)
_HEX = re.compile(r"\b(?:0x[0-9a-f]+|[0-9a-f]{6,})\b", re.I)
_NUM = re.compile(r"\d+(?:\.\d+)?")
_SPACE = re.compile(r"\s+")


def _hex(m: re.Match) -> str:
    # A long hex run counts only if it mixes digits and
    # letters, so words like "deadbeef" or "facade" and
    # plain numbers are left to the other rules.
    t = m.group()
    if t[:2].lower() == "0x":
        return "<hex>"
    has_digit = any(c.isdigit() for c in t)
    has_alpha = any(c.isalpha() for c in t)
    return "<hex>" if has_digit and has_alpha else t


def signature(line: str) -> str:
    """A log line without the parts that change between runs.

    Strips the Compose prefix, timestamps, trace ids, uuids,
    hex and numbers; lowercases; squeezes spaces.
    """
    s = COMPOSE_PREFIX.sub("", line.strip())
    s = _TRACE.sub(r"\1<trace>", s)
    s = _UUID.sub("<id>", s)
    s = _TIME.sub("<ts>", s)
    s = _HEX.sub(_hex, s)
    s = _NUM.sub("<n>", s)
    return _SPACE.sub(" ", s).strip().lower()
