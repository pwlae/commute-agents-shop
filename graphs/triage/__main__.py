"""Command line for the triage graph.

    python -m graphs.triage run T-01 [--record|--replay] [--resume]
    python -m graphs.triage eval [--record|--replay]

`eval` runs every ticket in tickets/expected.json and prints
`ticket expected got ok`, then `accuracy N/12`. It exits 1 if
any ticket that expects needs-human got something else.
"""
import argparse
import json
import sys

from .graph import run_ticket
from .run import ROOT

EXPECTED = ROOT / "tickets" / "expected.json"


def _mode(a):
    return "record" if a.record else "replay" if a.replay \
        else "live"


def _label(d):
    return d["branch"] + (f":{d['of']}" if d.get("of") else "")


def cmd_run(a):
    d = run_ticket(a.ticket, _mode(a), resume=a.resume)
    print(json.dumps(d, indent=1))
    return 0


def cmd_eval(a):
    expected = json.loads(EXPECTED.read_text())
    rows, right, human_wrong = [], 0, False
    for tid, e in expected.items():
        try:
            got = _label(run_ticket(tid, _mode(a)))
        except Exception as ex:  # noqa: BLE001
            got = f"error: {type(ex).__name__}: {ex}"[:60]
        want = _label(e)
        ok = got == want
        right += ok
        if e["branch"] == "needs-human" and not ok:
            human_wrong = True
        rows.append((tid, want, got, "yes" if ok else "NO"))
    head = ("ticket", "expected", "got", "ok")
    w = [max(len(r[i]) for r in rows + [head])
         for i in range(4)]
    for r in [head] + rows:
        print("  ".join(c.ljust(w[i])
                        for i, c in enumerate(r)).rstrip())
    print(f"accuracy {right}/{len(rows)}")
    if human_wrong:
        print("a needs-human ticket is wrong", file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="python -m graphs.triage")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("run", "eval"):
        s = sub.add_parser(name)
        if name == "run":
            s.add_argument("ticket")
            s.add_argument("--resume", action="store_true")
        g = s.add_mutually_exclusive_group()
        g.add_argument("--record", action="store_true",
                       help="call the LLM and tools, save "
                            "their answers")
        g.add_argument("--replay", action="store_true",
                       help="use saved answers only, offline")
    a = p.parse_args(argv)
    return cmd_run(a) if a.cmd == "run" else cmd_eval(a)


if __name__ == "__main__":
    sys.exit(main())
