"""Checks on the triage data: tickets, expected answers and
history. Catches drift between the history signatures and the
log lines the planted bugs really print."""
import json
import re
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from graphs.triage import tools  # noqa: E402

TICKETS = ROOT / "tickets"
HISTORY = ROOT / "ops" / "triage-history"
BUGS = ROOT / "PLANTED_BUGS.md"

BRANCHES = {"fix": 4, "duplicate": 2, "not-a-bug": 2,
            "needs-info": 2, "needs-human": 2}

# History entry -> text that picks its log line out of
# PLANTED_BUGS.md.
FROM_BUGS = {
    "H-02": "orders unavailable, keeping last value",
    "H-05": "captured for failed order",
}

# Lines printed in the windows of the `fix` tickets. No
# history signature may match them, or the graph would call
# those tickets duplicates.
FIX_LINES = ["reserved 3 items for order",
             "release order",
             "consumer stopped",
             "unknown sku chair-03 trace"]


def bug_lines():
    """Compose log lines quoted in PLANTED_BUGS.md."""
    return re.findall(r"^[\w.-]+-\d+ \| .*$", BUGS.read_text(),
                      re.M)


def bug_line(text):
    found = [ln for ln in bug_lines() if text in ln]
    if len(found) != 1:
        raise AssertionError(f"{len(found)} lines with {text!r}")
    return found[0]


def history():
    return {p.stem: json.loads(p.read_text())
            for p in sorted(HISTORY.glob("H-*.json"))}


def ticket(path):
    head, _, body = path.read_text().partition("\n\n")
    fields = dict(ln.split(": ", 1) for ln in head.splitlines())
    return fields, body


class HistoryTest(unittest.TestCase):
    def test_entries(self):
        h = history()
        self.assertEqual(sorted(h),
                         [f"H-0{i}" for i in range(1, 7)])
        for hid, e in h.items():
            self.assertEqual(set(e), {"id", "title", "signature",
                                      "branch", "resolved"})
            self.assertEqual(e["id"], hid)
            self.assertIn(e["branch"], BRANCHES)
            tools.parse_time(e["resolved"])
            # Already normalised: stable under signature().
            self.assertEqual(tools.signature(e["signature"]),
                             e["signature"])

    def test_signatures_match_planted_bug_lines(self):
        h = history()
        for hid, text in FROM_BUGS.items():
            with self.subTest(hid):
                self.assertEqual(
                    h[hid]["signature"],
                    tools.signature(bug_line(text)))

    def test_signatures_are_distinct(self):
        sigs = [e["signature"] for e in history().values()]
        self.assertEqual(len(sigs), len(set(sigs)))

    def test_fix_lines_match_no_history(self):
        sigs = {e["signature"] for e in history().values()}
        for text in FIX_LINES:
            with self.subTest(text):
                self.assertNotIn(
                    tools.signature(bug_line(text)), sigs)


class TicketsTest(unittest.TestCase):
    def test_files_and_headers(self):
        paths = sorted(TICKETS.glob("T-*.md"))
        self.assertEqual([p.stem for p in paths],
                         [f"T-{i:02d}" for i in range(1, 13)])
        for p in paths:
            with self.subTest(p.stem):
                fields, body = ticket(p)
                self.assertEqual(set(fields), {"id", "title",
                                               "reporter",
                                               "created"})
                self.assertEqual(fields["id"], p.stem)
                tools.parse_time(fields["created"])
                self.assertTrue(body.strip())

    def test_expected(self):
        exp = json.loads((TICKETS / "expected.json").read_text())
        self.assertEqual(sorted(exp),
                         [f"T-{i:02d}" for i in range(1, 13)])
        self.assertEqual(Counter(e["branch"] for e in exp.values()),
                         Counter(BRANCHES))
        h = history()
        for tid, e in exp.items():
            with self.subTest(tid):
                self.assertTrue(e["why"])
                if e["branch"] == "duplicate":
                    self.assertIn(e["of"], FROM_BUGS)
                    self.assertIn(e["of"], h)
                else:
                    self.assertNotIn("of", e)


if __name__ == "__main__":
    unittest.main()
