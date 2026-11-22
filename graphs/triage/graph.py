"""The graph: a fixed list of nodes, state saved after each."""
import shutil
from .run import Run
from . import nodes

NODES = [("intake", nodes.intake),
         ("discovery", nodes.discovery),
         ("similar", nodes.similar),
         ("decide", nodes.decide),
         ("note", nodes.note)]

def run_ticket(ticket_id, mode="live", resume=False, **kw):
    run = Run(ticket_id, mode, **kw)
    if not resume:
        run.reset()
        if mode == "record" and run.rec.exists():
            # A fresh recording: no stale keys from older runs.
            shutil.rmtree(run.rec)
    for name, fn in NODES:
        if name in run.state["done"]:
            continue
        run.state[name] = fn(run)
        run.state["done"].append(name)
        run.save()
    return run.state["decide"]
