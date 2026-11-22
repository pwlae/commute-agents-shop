import io
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from graphs.triage import tools  # noqa: E402

# Two runs of the same error in each of the four log formats:
# only timestamps, ids, trace ids and numbers differ.
FORMATS = {
    "gateway-json": (
        '{"time":"2026-11-20T09:14:00.123Z","level":"error",'
        '"msg":"upstream failed","route":"/orders",'
        '"x-trace-id":"7f3a9c21e4b84d0f","order_id":'
        '"3f2b8c1e-9d4a-4b7e-8f1a-2c3d4e5f6a7b","status":502}',
        '{"time":"2026-11-21T17:02:41.007Z","level":"error",'
        '"msg":"upstream failed","route":"/orders",'
        '"x-trace-id":"0c4be81d2a9f4471","order_id":'
        '"a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d","status":502}',
        '{"time":"<ts>","level":"error","msg":"upstream failed",'
        '"route":"/orders","x-trace-id":"<trace>",'
        '"order_id":"<id>","status":<n>}',
    ),
    "orders-logfmt": (
        'ts=2026-11-20T09:14:00Z level=error msg="charge failed"'
        ' order_id=3f2b8c1e-9d4a-4b7e-8f1a-2c3d4e5f6a7b'
        ' x-trace-id=7f3a9c21e4b84d0f attempt=2 took_ms=2004',
        'ts=2026-11-21T17:02:41Z level=error msg="charge failed"'
        ' order_id=a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'
        ' x-trace-id=0c4be81d2a9f4471 attempt=3 took_ms=2011',
        'ts=<ts> level=error msg="charge failed" order_id=<id>'
        ' x-trace-id=<trace> attempt=<n> took_ms=<n>',
    ),
    "payments-python": (
        '2026-11-20 09:14:00,123 ERROR payments.app charge'
        ' 18 for order 3f2b8c1e-9d4a-4b7e-8f1a-2c3d4e5f6a7b'
        ' failed: provider timeout after 2.0s',
        '2026-11-21 17:02:41,950 ERROR payments.app charge'
        ' 431 for order a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d'
        ' failed: provider timeout after 2.5s',
        '<ts> error payments.app charge <n> for order <id>'
        ' failed: provider timeout after <n>s',
    ),
    "inventory-plain": (
        '2026/11/20 09:14:00 ERROR consume inventory.reserve.v1'
        ' partition 2 offset 1187: invalid character \'o\''
        ' looking for beginning of value trace-id=7f3a9c21'
        ' at 0xc000123abc',
        '2026/11/21 17:02:41 ERROR consume inventory.reserve.v1'
        ' partition 0 offset 99: invalid character \'o\''
        ' looking for beginning of value trace-id=b81d2a9f'
        ' at 0xc0004f00e0',
        '<ts> error consume inventory.reserve.v<n> partition <n>'
        ' offset <n>: invalid character \'o\' looking for'
        ' beginning of value trace-id=<trace> at <hex>',
    ),
}


class SignatureTest(unittest.TestCase):
    def test_four_formats_stable(self):
        for name, (a, b, want) in FORMATS.items():
            with self.subTest(name):
                self.assertEqual(tools.signature(a), want)
                self.assertEqual(tools.signature(b), want)

    def test_formats_stay_distinct(self):
        sigs = {tools.signature(a) for a, _, _ in FORMATS.values()}
        self.assertEqual(len(sigs), len(FORMATS))

    def test_compose_prefix_and_spaces(self):
        raw = FORMATS["orders-logfmt"][0]
        self.assertEqual(
            tools.signature("orders-1  | " + raw + "  "),
            tools.signature(raw))

    def test_lowercase_and_words_kept(self):
        self.assertEqual(
            tools.signature("WARN Stock low for MAT-01: facade"),
            "warn stock low for mat-<n>: facade")

    def test_different_errors_differ(self):
        self.assertNotEqual(
            tools.signature("ERROR charge failed order 1"),
            tools.signature("ERROR refund failed order 1"))


DEPLOY_ROWS = [
    {"service": "inventory", "git_sha": "a1b2c3d",
     "time": "2026-11-20T10:05:00Z", "note": "bad sku case"},
    {"service": "orders", "git_sha": "b2c3d4e",
     "time": "2026-11-19T08:00:00Z", "note": "saga metrics"},
    {"service": "payments", "git_sha": "c3d4e5f",
     "time": "2026-11-20T09:00:00Z", "note": "reconciler"},
    {"service": "orders", "git_sha": "d4e5f6a",
     "time": "2026-11-20T09:30:00Z", "note": "log fields"},
    {"service": "inventory", "git_sha": "e5f6a7b",
     "time": "2026-11-21T12:00:00Z", "note": "rollback"},
]


class DeploysTest(unittest.TestCase):
    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.path = Path(d.name) / "deploys.jsonl"
        lines = [json.dumps(r) for r in DEPLOY_ROWS]
        self.path.write_text("\n".join(lines) + "\n\n")

    def shas(self, services, since, until):
        rows = tools.deploys(services, since, until, path=self.path)
        return [r["git_sha"] for r in rows]

    def test_window_and_service(self):
        self.assertEqual(
            self.shas(["inventory", "orders"],
                      "2026-11-20T00:00:00Z", "2026-11-20T23:59:59Z"),
            ["d4e5f6a", "a1b2c3d"])

    def test_window_is_inclusive(self):
        self.assertEqual(
            self.shas(["inventory"],
                      "2026-11-20T10:05:00Z", "2026-11-20T10:05:00Z"),
            ["a1b2c3d"])

    def test_outside_window_is_empty(self):
        self.assertEqual(
            self.shas(["inventory"],
                      "2026-11-18T00:00:00Z", "2026-11-19T23:00:00Z"),
            [])

    def test_empty_services_means_all_sorted(self):
        self.assertEqual(
            self.shas([], "2026-11-19T00:00:00Z",
                      "2026-11-22T00:00:00Z"),
            ["b2c3d4e", "c3d4e5f", "d4e5f6a", "a1b2c3d", "e5f6a7b"])

    def test_offsets_and_naive_times(self):
        # 11:05+01:00 is 10:05Z; a naive time is read as UTC.
        self.assertEqual(
            self.shas(["inventory"], "2026-11-20T11:05:00+01:00",
                      "2026-11-20T10:05:00"),
            ["a1b2c3d"])

    def test_missing_file(self):
        rows = tools.deploys([], "2026-11-01T00:00:00Z",
                             "2026-11-30T00:00:00Z",
                             path=self.path.with_name("none.jsonl"))
        self.assertEqual(rows, [])


class LogsTest(unittest.TestCase):
    OUT = "\n".join([
        "orders-1  | ts=2026-11-20T09:14:00Z level=info msg=ok",
        "orders-1  | ts=2026-11-20T09:14:01Z level=error msg=a",
        "orders-1  | ts=2026-11-20T09:14:02Z level=WARN msg=b",
        "orders-1  | Traceback (most recent call last):",
        "orders-1  | panic: runtime error",
        "orders-1  | ts=2026-11-20T09:14:03Z level=debug msg=c",
    ]) + "\n"

    def run_logs(self, **kw):
        done = subprocess.CompletedProcess([], 0, self.OUT, "")
        with mock.patch.object(tools.subprocess, "run",
                               return_value=done) as run:
            lines = tools.logs("orders", "2026-11-20T09:00:00Z",
                               "2026-11-20T10:00:00Z", **kw)
        return lines, run

    def test_command_and_filter(self):
        lines, run = self.run_logs()
        self.assertEqual(
            run.call_args.args[0],
            ["docker", "compose", "logs", "--no-color",
             "--since", "2026-11-20T09:00:00Z",
             "--until", "2026-11-20T10:00:00Z", "orders"])
        self.assertEqual(run.call_args.kwargs["cwd"], tools.ROOT)
        self.assertEqual(lines, [
            "ts=2026-11-20T09:14:01Z level=error msg=a",
            "ts=2026-11-20T09:14:02Z level=WARN msg=b",
            "Traceback (most recent call last):",
            "panic: runtime error",
        ])

    def test_limit_keeps_newest(self):
        lines, _ = self.run_logs(limit=2)
        self.assertEqual(lines, ["Traceback (most recent call last):",
                                 "panic: runtime error"])

    def test_failure_raises(self):
        bad = subprocess.CompletedProcess([], 1, "", "no such service")
        with mock.patch.object(tools.subprocess, "run",
                               return_value=bad):
            with self.assertRaises(RuntimeError):
                tools.logs("nope", "2026-11-20T09:00:00Z",
                           "2026-11-20T10:00:00Z")


class MetricTest(unittest.TestCase):
    def test_query_range(self):
        body = {"status": "success", "data": {
            "resultType": "matrix", "result": [
                {"metric": {"service": "orders"},
                 "values": [[1795165200, "1"], [1795165230, "3"]]},
            ]}}
        resp = io.BytesIO(json.dumps(body).encode())
        with mock.patch.object(tools.urllib.request, "urlopen",
                               return_value=resp) as op:
            rows = tools.metric(
                "sum(rate(http_requests_total[1m])) by (service)",
                "2026-11-20T09:00:00Z", "2026-11-20T10:00:00Z")
        self.assertEqual(rows, [[{"service": "orders"},
                                 [[1795165200, "1"],
                                  [1795165230, "3"]]]])
        url = op.call_args.args[0]
        self.assertTrue(url.startswith(
            "http://localhost:9090/api/v1/query_range?"))
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        self.assertEqual(q["start"], ["1795165200.0"])
        self.assertEqual(q["end"], ["1795168800.0"])
        self.assertEqual(q["step"], ["30s"])
        self.assertEqual(
            q["query"],
            ["sum(rate(http_requests_total[1m])) by (service)"])

    def test_error_raises(self):
        body = {"status": "error", "error": "parse error"}
        resp = io.BytesIO(json.dumps(body).encode())
        with mock.patch.object(tools.urllib.request, "urlopen",
                               return_value=resp):
            with self.assertRaises(RuntimeError):
                tools.metric("up{", "2026-11-20T09:00:00Z",
                             "2026-11-20T10:00:00Z")


if __name__ == "__main__":
    unittest.main()
