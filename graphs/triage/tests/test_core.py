"""Core of the triage graph: resume, budget, replay, rules.

Fully offline: the LLM API, Docker and Prometheus are faked or
replayed from recordings written into temp dirs.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from graphs.triage import (graph, llm, nodes, rules,  # noqa: E402
                           tools)
from graphs.triage.run import BudgetExceeded, Run  # noqa: E402

INTAKE = {"symptoms": ["something weird"],
          "services": ["gateway", "orders"],
          "since": "2026-11-18T08:00:00Z",
          "until": "2026-11-19T08:10:00Z"}
DECIDE = {"branch": "needs-info", "confidence": 0.8,
          "evidence": [], "why": "nothing to look for"}


def anthropic_reply(text):
    return {"content": [{"type": "text", "text": text}]}


class FakeAPI:
    """Stands in for llm._post: answers by node."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, body, headers):
        system = body["system"]
        if system == nodes.INTAKE_SYSTEM:
            self.calls.append("intake")
            text = "```json\n" + json.dumps(INTAKE) + "\n```"
        elif system == nodes.DISCOVERY_SYSTEM:
            self.calls.append("discovery")
            text = "Nothing stands out."
        else:
            self.calls.append("decide")
            text = json.dumps(DECIDE)
        return anthropic_reply(text)


def no_network(*a, **kw):
    raise AssertionError("network or subprocess used")


class TempDirs(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.runs = Path(tmp.name) / "runs"
        self.rec = Path(tmp.name) / "rec"
        self.kw = {"runs_dir": self.runs, "rec_dir": self.rec}


class ResumeTest(TempDirs):
    def test_resume_skips_done_nodes(self):
        api = FakeAPI()
        failed = []

        def flaky_discovery(run):
            if not failed:
                failed.append(1)
                raise RuntimeError("crash in discovery")
            return nodes.discovery(run)

        patched = [(n, flaky_discovery if n == "discovery" else f)
                   for n, f in graph.NODES]
        with mock.patch.object(graph, "NODES", patched), \
                mock.patch.object(llm, "_post", api), \
                mock.patch.dict(os.environ,
                                {"ANTHROPIC_API_KEY": "test"}), \
                mock.patch.object(tools, "logs",
                                  return_value=[]), \
                mock.patch.object(tools, "metric",
                                  return_value=[]):
            with self.assertRaises(RuntimeError):
                graph.run_ticket("T-01", **self.kw)
            state = Run("T-01", **self.kw).state
            self.assertEqual(state["done"], ["intake"])
            self.assertEqual(state["llm_calls"], 1)

            d = graph.run_ticket("T-01", resume=True, **self.kw)

        self.assertEqual(api.calls,
                         ["intake", "discovery", "decide"])
        state = Run("T-01", **self.kw).state
        self.assertEqual(state["done"], [n for n, _ in graph.NODES])
        self.assertEqual(state["llm_calls"], 3)
        self.assertEqual(d["branch"], "needs-info")
        self.assertTrue((self.runs / "T-01/note.md").exists())
        self.assertTrue((self.runs / "T-01/decision.json").exists())


class BudgetTest(TempDirs):
    def test_budget(self):
        run = Run("T-01", max_llm_calls=1, **self.kw)
        api = mock.Mock(return_value=anthropic_reply("ok"))
        with mock.patch.object(llm, "_post", api), \
                mock.patch.dict(os.environ,
                                {"ANTHROPIC_API_KEY": "test"}):
            self.assertEqual(llm.llm(run, "a", "s", "p"), "ok")
            with self.assertRaises(BudgetExceeded):
                llm.llm(run, "b", "s", "p")
        self.assertEqual(api.call_count, 1)
        self.assertEqual(run.state["llm_calls"], 1)


class ReplayTest(TempDirs):
    def write_recordings(self, ticket):
        rec = {"intake.llm": json.dumps(INTAKE),
               "discovery.deploys.all": [],
               "discovery.llm": "Nothing stands out.",
               "decide.llm": json.dumps(DECIDE)}
        for s in INTAKE["services"]:
            rec[f"discovery.logs.{s}"] = []
        for name in nodes.METRICS:
            rec[f"discovery.metric.{name}"] = []
        d = self.rec / ticket
        d.mkdir(parents=True)
        for k, v in rec.items():
            (d / f"{k}.json").write_text(json.dumps(v))

    def test_replay_offline(self):
        self.write_recordings("T-01")
        with mock.patch.object(urllib.request, "urlopen",
                               no_network), \
                mock.patch.object(subprocess, "run", no_network), \
                mock.patch.dict(os.environ, clear=True):
            d = graph.run_ticket("T-01", mode="replay", **self.kw)
        self.assertEqual(d["branch"], "needs-info")
        self.assertEqual(Run("T-01", **self.kw)
                         .state["llm_calls"], 0)

    def test_record_then_replay(self):
        api = FakeAPI()
        with mock.patch.object(llm, "_post", api), \
                mock.patch.dict(os.environ,
                                {"ANTHROPIC_API_KEY": "test"}), \
                mock.patch.object(tools, "logs",
                                  return_value=["level=warn x"]), \
                mock.patch.object(tools, "metric",
                                  side_effect=OSError("down")):
            first = graph.run_ticket("T-01", mode="record",
                                     **self.kw)
        with mock.patch.object(urllib.request, "urlopen",
                               no_network), \
                mock.patch.object(subprocess, "run", no_network):
            again = graph.run_ticket("T-01", mode="replay",
                                     **self.kw)
        self.assertEqual(first, again)
        err = json.loads((self.rec / "T-01" /
                          "discovery.metric.alerts.json")
                         .read_text())
        self.assertIn("down", err["error"])


class RulesTest(unittest.TestCase):
    def test_money_rule_overrides_llm(self):
        d = rules.apply("I was charged twice for one order",
                        {"match": "H-05"},
                        {"branch": "fix", "confidence": 0.99,
                         "evidence": ["x"], "why": "bug"})
        self.assertEqual(d["branch"], "needs-human")

    def test_money_rule_matches_expected(self):
        exp = json.loads(
            (ROOT / "tickets/expected.json").read_text())
        money = set()
        for tid, e in exp.items():
            text = nodes.load_ticket(tid)["text"]
            hit = bool(rules.MONEY.search(text))
            if hit:
                money.add(tid)
            with self.subTest(tid):
                self.assertEqual(hit,
                                 e["branch"] == "needs-human")
        self.assertEqual(money, {"T-04", "T-08"})

    def test_low_confidence(self):
        low = {"branch": "fix", "confidence": 0.3}
        self.assertEqual(
            rules.apply("slow sometimes", {"match": None},
                        {**low, "evidence": []})["branch"],
            "needs-info")
        self.assertEqual(
            rules.apply("slow sometimes", {"match": None},
                        {**low, "evidence": ["p95 2.1 s"]})
            ["branch"], "needs-human")

    def test_duplicate_before_llm(self):
        d = rules.apply("payments warnings", {"match": "H-02"},
                        {"branch": "fix", "confidence": 0.9})
        self.assertEqual((d["branch"], d["of"]),
                         ("duplicate", "H-02"))


class SimilarTest(TempDirs):
    def similar_for(self, ticket, raw_logs):
        run = Run(ticket, **self.kw)
        run.state["discovery"] = {"raw_logs": raw_logs}
        return nodes.similar(run)["match"]

    def test_quoted_lines_find_history(self):
        self.assertEqual(self.similar_for("T-07", {}), "H-02")
        self.assertEqual(self.similar_for("T-09", {}), "H-05")

    def test_fix_and_vague_tickets_match_nothing(self):
        for t in ("T-01", "T-02", "T-05", "T-06", "T-12"):
            with self.subTest(t):
                self.assertIsNone(self.similar_for(t, {}))

    def test_discovery_lines_find_history(self):
        line = ("2026-11-22 09:35:46,967 WARNING "
                "payments.reconciler captured for failed order "
                "charge_id=ch_a959735335fb402cb1b5a47e "
                "order_id=bb43913f-32ae-4604-bee9-be5c4a4adb22")
        self.assertEqual(
            self.similar_for("T-01", {"payments": [line],
                                      "orders": {"error": "x"}}),
            "H-05")


class ParseJsonTest(unittest.TestCase):
    def test_fences_and_prose(self):
        self.assertEqual(nodes.parse_json('```json\n{"a": 1}\n```'),
                         {"a": 1})
        self.assertEqual(nodes.parse_json('Sure: {"a": 1}.'),
                         {"a": 1})


if __name__ == "__main__":
    unittest.main()
