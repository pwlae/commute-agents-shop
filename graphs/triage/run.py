"""One run of the graph for one ticket: state on disk, the
LLM budget, and record/replay of everything from outside."""
import json, os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REC = ROOT / "graphs/triage/recordings"

class BudgetExceeded(Exception):
    pass

class Run:
    def __init__(self, ticket_id, mode="live",
                 runs_dir=ROOT / "runs", rec_dir=REC,
                 max_llm_calls=6):
        assert mode in ("live", "record", "replay")
        self.mode, self.max = mode, max_llm_calls
        self.dir = Path(runs_dir) / ticket_id
        self.rec = Path(rec_dir) / ticket_id
        self.path = self.dir / "state.json"
        self.state = {"ticket": ticket_id,
                      "done": [], "llm_calls": 0}
        if self.path.exists():
            self.state = json.loads(self.path.read_text())

    def reset(self):
        self.state = {"ticket": self.state["ticket"],
                      "done": [], "llm_calls": 0}
        self.save()

    def save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, indent=1))
        os.replace(tmp, self.path)

    def spend_llm_call(self):
        if self.state["llm_calls"] >= self.max:
            raise BudgetExceeded(self.state["ticket"])
        self.state["llm_calls"] += 1

    def outside(self, key, fn):
        p = self.rec / f"{key}.json"
        if self.mode == "replay":
            return json.loads(p.read_text())
        value = fn()
        if self.mode == "record":
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(value, indent=1))
        return value
