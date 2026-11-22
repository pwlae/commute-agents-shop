"""The five nodes of the triage graph.

Each node takes the `run`, reads earlier results from
`run.state` and returns a JSON-able dict. Everything that
leaves the process (LLM, Docker, Prometheus, files that change
while the stack runs) goes through `run.outside`, so a run can
be recorded once and replayed offline.
"""
import json
import re
from collections import Counter
from datetime import timedelta

from . import rules, tools
from .llm import llm
from .run import ROOT

TICKETS = ROOT / "tickets"
HISTORY = ROOT / "ops" / "triage-history"
SERVICES = ["gateway", "orders", "payments", "inventory",
            "card-provider"]
BRANCHES = ["fix", "not-a-bug", "needs-info", "needs-human"]

# name -> PromQL. Names are part of the recording keys.
METRICS = {
    "alerts": 'ALERTS{alertstate="firing"}',
    "stock_drift": "inventory_stock_drift",
    "consumer_lag": "inventory_consumer_lag",
    "pending_oldest": "orders_pending_oldest_seconds",
    "saga": "sum by (to) "
            "(increase(orders_saga_transitions_total[5m]))",
    "duplicate_charges":
        "sum(increase(payments_duplicate_charges_total[5m]))",
    "captured_failed": "payments_captured_for_failed_orders",
    "errors_5xx": "sum by (service) "
                  '(rate(http_requests_total{code=~"5.."}[5m]))',
    "p95_seconds": "histogram_quantile(0.95, sum by "
                   "(service, le) (rate("
                   "http_request_duration_seconds_bucket"
                   '{route!~"/metrics|/healthz"}[5m])))',
    "card_errors":
        "sum(increase(card_provider_errors_total[5m]))",
}
METRIC_STEP = "60s"
DEPLOY_LOOKBACK = timedelta(hours=6)
FENCE = re.compile(r"```[\w-]*\n(.*?)```", re.S)


# ---------- helpers ----------

def load_ticket(ticket_id):
    """Header fields, body, and `text` (title + body)."""
    raw = (TICKETS / f"{ticket_id}.md").read_text()
    head, _, body = raw.partition("\n\n")
    fields = dict(ln.split(": ", 1) for ln in head.splitlines())
    fields["body"] = body.strip()
    fields["text"] = fields["title"] + "\n\n" + fields["body"]
    return fields


def parse_json(text):
    """The first JSON object in an LLM answer, fences or not."""
    s = text.strip()
    m = FENCE.search(s)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except ValueError:
        i, j = s.find("{"), s.rfind("}")
        if i < 0 or j <= i:
            raise
        return json.loads(s[i:j + 1])


def iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe(fn, *args):
    """A tool result, or {"error": ...} if the tool failed, so
    a missing service is a fact, not a crash."""
    try:
        return fn(*args)
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def _series_facts(series):
    """[labels, [[t, "v"], ...]] -> labels with min/max/last."""
    out = []
    for labels, values in series:
        nums = []
        for _, v in values:
            try:
                nums.append(float(v))
            except ValueError:
                pass
        if nums:
            labels = {k: v for k, v in labels.items()
                      if k not in ("__name__", "instance", "job")}
            out.append({"labels": labels, "min": min(nums),
                        "max": max(nums), "last": nums[-1],
                        "points": len(nums)})
    return out


def _log_facts(lines, keep=25):
    """Unique lines by signature, with counts, newest kept."""
    count, example = Counter(), {}
    for ln in lines:
        sig = tools.signature(ln)
        count[sig] += 1
        example[sig] = ln
    sigs = list(example)[-keep:]
    return [{"count": count[s], "line": example[s][:400]}
            for s in sigs]


# ---------- nodes ----------

INTAKE_SYSTEM = (
    "You read support tickets for an online shop and turn "
    "them into a search plan. Services: "
    + ", ".join(SERVICES) + ". Answer with one JSON object "
    "only, no prose.")


def intake(run):
    t = load_ticket(run.state["ticket"])
    prompt = (
        f"Ticket {t['id']}, reported by {t['reporter']}, "
        f"created {t['created']}.\n\n{t['text']}\n\n"
        "Return JSON with keys:\n"
        '  "symptoms": list of short strings, what the '
        "reporter saw;\n"
        '  "services": the services worth looking at, from '
        "the list above;\n"
        '  "since", "until": the UTC time window to search, '
        "ISO 8601 with Z. Use the ticket's times; if it "
        "gives none, use the 24 hours before it was "
        "created.")
    raw = llm(run, "intake.llm", INTAKE_SYSTEM, prompt)
    try:
        got = parse_json(raw)
    except ValueError:
        got = {}
    created = tools.parse_time(t["created"])
    try:
        since = tools.parse_time(got["since"])
        until = tools.parse_time(got["until"])
    except (KeyError, TypeError, ValueError):
        since, until = created - timedelta(hours=24), created
    if until < since:
        since, until = until, since
    services = [s for s in got.get("services") or []
                if s in SERVICES] or SERVICES[:4]
    symptoms = [str(s) for s in got.get("symptoms") or []]
    return {"symptoms": symptoms, "services": services,
            "since": iso(since), "until": iso(until)}


DISCOVERY_SYSTEM = (
    "You summarise what an online shop's deploys, logs and "
    "metrics show in a time window. Be short and factual: "
    "name the lines, numbers and deploys that stand out, "
    "and say plainly if nothing does.")


def discovery(run):
    i = run.state["intake"]
    since, until = i["since"], i["until"]
    deploy_since = iso(tools.parse_time(since) - DEPLOY_LOOKBACK)
    deploys = run.outside(
        "discovery.deploys.all",
        lambda: _safe(tools.deploys, [], deploy_since, until))
    logs = {}
    for s in i["services"]:
        logs[s] = run.outside(
            f"discovery.logs.{s}",
            lambda s=s: _safe(tools.logs, s, since, until))
    metrics = {}
    for name, q in METRICS.items():
        metrics[name] = run.outside(
            f"discovery.metric.{name}",
            lambda q=q: _safe(tools.metric, q, since, until,
                              METRIC_STEP))

    facts = {
        "window": {"since": since, "until": until},
        "deploys": deploys,
        "logs": {s: v if isinstance(v, dict) else _log_facts(v)
                 for s, v in logs.items()},
        "metrics": {n: v if isinstance(v, dict)
                    else _series_facts(v)
                    for n, v in metrics.items()},
    }
    signals = (
        (len(deploys) if isinstance(deploys, list) else 0)
        + sum(len(v) for v in logs.values()
              if isinstance(v, list))
        + sum(1 for v in facts["metrics"].values()
              if isinstance(v, list)
              for s in v if s["max"] > 0))
    prompt = (
        "Symptoms reported: " + "; ".join(i["symptoms"])
        + "\n\nFacts (JSON):\n" + json.dumps(facts, indent=1)
        + "\n\nSummarise in at most 8 lines.")
    summary = llm(run, "discovery.llm", DISCOVERY_SYSTEM,
                  prompt).strip()
    return {"deploys": deploys, "logs": facts["logs"],
            "metrics": facts["metrics"], "summary": summary,
            "signals": signals, "raw_logs": logs}


def _bare(sig):
    """A signature without timestamps, for lines a person
    pasted into a ticket without them."""
    return " ".join(sig.replace("<ts>", " ").split())


def similar(run):
    t = load_ticket(run.state["ticket"])
    lines = []
    for v in run.state["discovery"]["raw_logs"].values():
        if isinstance(v, list):
            lines += v
    # Lines quoted in the ticket count too.
    for block in FENCE.findall(t["body"]):
        lines += [ln for ln in block.splitlines() if ln.strip()]
    sigs = Counter(_bare(tools.signature(ln)) for ln in lines)
    hits = {}
    for p in sorted(HISTORY.glob("H-*.json")):
        h = json.loads(p.read_text())
        n = sigs.get(_bare(h["signature"]), 0)
        if n:
            hits[h["id"]] = n
    match = max(hits, key=hits.get) if hits else None
    return {"match": match, "hits": hits}


DECIDE_SYSTEM = (
    "You triage support tickets for an online shop. Pick "
    "one branch:\n"
    "  fix: a defect in the shop, and the facts point at "
    "where it is;\n"
    "  not-a-bug: the shop behaved as designed;\n"
    "  needs-info: the ticket is too vague to look for "
    "anything and the facts show nothing that stands out;\n"
    "  needs-human: something is wrong, but the facts are "
    "not enough to say what.\n"
    "Answer with one JSON object only, no prose.")


def decide(run):
    t = load_ticket(run.state["ticket"])
    i, d = run.state["intake"], run.state["discovery"]
    sim = run.state["similar"]
    facts = {"deploys": d["deploys"], "logs": d["logs"],
             "metrics": d["metrics"]}
    prompt = (
        f"Ticket {t['id']} ({t['reporter']}, created "
        f"{t['created']}):\n\n{t['text']}\n\n"
        f"Window searched: {i['since']} to {i['until']}, "
        f"services {', '.join(i['services'])}.\n\n"
        f"Summary of the facts:\n{d['summary']}\n\n"
        f"Facts (JSON):\n{json.dumps(facts, indent=1)}\n\n"
        "Return JSON with keys:\n"
        '  "branch": one of ' + ", ".join(BRANCHES) + ";\n"
        '  "confidence": 0 to 1;\n'
        '  "evidence": list of the facts above that support '
        "the branch, quoted (empty if none);\n"
        '  "why": one or two sentences.')
    raw = llm(run, "decide.llm", DECIDE_SYSTEM, prompt,
              max_tokens=1000)
    try:
        proposal = parse_json(raw)
    except ValueError:
        proposal = {}
    if not isinstance(proposal, dict) \
            or proposal.get("branch") not in BRANCHES:
        proposal = {"confidence": 0, "evidence": [],
                    "why": "no usable answer", "raw": raw}
    try:
        proposal["confidence"] = float(
            proposal.get("confidence", 0))
    except (TypeError, ValueError):
        proposal["confidence"] = 0.0
    decision = rules.apply(t["text"], sim, proposal)
    decision["proposal"] = proposal
    return decision


def note(run):
    t = load_ticket(run.state["ticket"])
    dec, d = run.state["decide"], run.state["discovery"]
    head = dec["branch"] + (f" of {dec['of']}"
                            if dec.get("of") else "")
    lines = [f"# {t['id']}: {t['title']}", "",
             f"**Branch:** {head}", "",
             f"**Why:** {dec.get('why', '')}", ""]
    if dec.get("evidence"):
        lines += ["**Evidence:**", ""]
        lines += [f"- {e}" for e in dec["evidence"]] + [""]
    p = dec.get("proposal", {})
    lines += [f"**LLM proposal:** {p.get('branch', '-')} "
              f"(confidence {p.get('confidence', 0)})", "",
              "**What discovery found:**", "", d["summary"], ""]
    run.dir.mkdir(parents=True, exist_ok=True)
    path = run.dir / "note.md"
    path.write_text("\n".join(lines))
    (run.dir / "decision.json").write_text(
        json.dumps(dec, indent=1))
    return {"path": str(path)}
