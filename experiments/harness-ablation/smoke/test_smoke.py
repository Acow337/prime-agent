import json
import http.server
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path


SMOKE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SMOKE_DIR))

import tasks
import budget_proxy


class TaskFixtureTests(unittest.TestCase):
    def test_materialization_is_deterministic(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            left = tasks.materialize_tasks(Path(first))
            tasks.materialize_tasks(Path(second))
            self.assertEqual(
                [case.task_id for case in left],
                ["t1-structured", "t2-log-correlation", "t3-parser-fix"],
            )
            self.assertEqual(
                tasks.fixture_digest(Path(first)), tasks.fixture_digest(Path(second))
            )

    def test_json_reference_answers_pass_and_malformed_answers_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            cases = tasks.materialize_tasks(Path(directory))
            for case in cases[:2]:
                with self.subTest(case=case.task_id):
                    result = tasks.verify(case, case.reference_output)
                    self.assertEqual((result["score"], result["success"]), (1.0, True))
                    self.assertFalse(tasks.verify(case, "not valid output")["success"])

    def test_parser_fixture_starts_red_and_reference_fix_turns_it_green(self):
        with tempfile.TemporaryDirectory() as directory:
            case = tasks.materialize_tasks(Path(directory))[2]
            self.assertFalse(tasks.verify(case, "")["success"])
            tasks.apply_reference_fix(case)
            result = tasks.verify(case, "fixed")
            self.assertEqual((result["score"], result["success"]), (1.0, True))

    def test_log_fixture_is_approximately_ten_kilobytes(self):
        with tempfile.TemporaryDirectory() as directory:
            case = tasks.materialize_tasks(Path(directory))[1]
            size = (case.workdir / "events.log").stat().st_size
            self.assertGreaterEqual(size, 9_000)
            self.assertLessEqual(size, 13_000)


class ManifestTests(unittest.TestCase):
    def test_budget_values_match_the_approved_design(self):
        manifest = json.loads((SMOKE_DIR / "manifest.json").read_text())
        self.assertEqual(manifest["max_provider_requests"], 6)
        self.assertEqual(manifest["response_tokens"], 1024)
        self.assertEqual(manifest["hard_budget_cny"], 10.0)


class BudgetPolicyTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((SMOKE_DIR / "manifest.json").read_text())

    def test_six_full_context_requests_fit_and_seventh_is_refused(self):
        policy = budget_proxy.BudgetPolicy.from_manifest(self.manifest)
        ledger = budget_proxy.UsageLedger(policy)
        self.assertLess(policy.worst_case_request_cny() * 6, 10.0)
        for _ in range(6):
            ledger.admit({"max_tokens": 1024})
        with self.assertRaises(budget_proxy.BudgetExceeded):
            ledger.admit({"max_tokens": 1024})

    def test_excessive_response_limit_is_refused(self):
        policy = budget_proxy.BudgetPolicy.from_manifest(self.manifest)
        ledger = budget_proxy.UsageLedger(policy)
        with self.assertRaises(budget_proxy.BudgetExceeded):
            ledger.admit({"max_tokens": 1025})

    def test_usage_cost_uses_catalog_prices_and_fx(self):
        policy = budget_proxy.BudgetPolicy.from_manifest(self.manifest)
        ledger = budget_proxy.UsageLedger(policy)
        ledger.admit({"max_tokens": 1024})
        ledger.record_usage({"prompt_tokens": 1_000_000, "completion_tokens": 1_000})
        summary = ledger.summary()
        self.assertAlmostEqual(summary["actual_cost_usd"], 0.1506)
        self.assertAlmostEqual(summary["actual_cost_cny"], 1.2048)

    def test_authorization_is_removed_case_insensitively(self):
        clean = budget_proxy.redact_headers(
            {"Authorization": "Bearer secret", "authorization": "second", "X-Test": "ok"}
        )
        self.assertEqual(clean, {"X-Test": "ok"})


class _FakeUpstreamHandler(http.server.BaseHTTPRequestHandler):
    calls = 0
    response_body = (
        b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":42,'
        b'"completion_tokens":7}}\n\n'
        b"data: [DONE]\n\n"
    )

    def do_POST(self):
        type(self).calls += 1
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, *_args):
        pass


class ProxyTests(unittest.TestCase):
    def setUp(self):
        _FakeUpstreamHandler.calls = 0
        self.upstream = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _FakeUpstreamHandler
        )
        self.upstream_thread = threading.Thread(
            target=self.upstream.serve_forever, daemon=True
        )
        self.upstream_thread.start()
        manifest = json.loads((SMOKE_DIR / "manifest.json").read_text())
        policy = budget_proxy.BudgetPolicy.from_manifest(manifest)
        upstream_url = (
            f"http://127.0.0.1:{self.upstream.server_port}/chat/completions"
        )
        self.proxy = budget_proxy.start_proxy(policy, upstream=upstream_url)

    def tearDown(self):
        self.proxy.close()
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join()

    def _post(self, max_tokens):
        request = urllib.request.Request(
            f"{self.proxy.base_url}/chat/completions",
            data=json.dumps({"max_tokens": max_tokens, "stream": True}).encode(),
            headers={
                "Authorization": "Bearer test-only",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        return urllib.request.urlopen(request, timeout=2)

    def test_stream_is_forwarded_byte_for_byte_and_usage_is_recorded(self):
        with self._post(1024) as response:
            self.assertEqual(response.read(), _FakeUpstreamHandler.response_body)
        self.assertEqual(
            self.proxy.ledger.summary(),
            {
                "request_count": 1,
                "prompt_tokens": 42,
                "completion_tokens": 7,
                "actual_cost_usd": 0.0000105,
                "actual_cost_cny": 0.000084,
                "conservative_cost_cny": 1.2049152,
                "rejection_count": 0,
                "error_categories": {},
            },
        )

    def test_seventh_request_is_402_and_never_reaches_upstream(self):
        for _ in range(6):
            with self._post(1024) as response:
                response.read()
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            self._post(1024)
        self.assertEqual(rejected.exception.code, 402)
        rejected.exception.close()
        self.assertEqual(_FakeUpstreamHandler.calls, 6)

    def test_excessive_response_limit_is_400_and_never_reaches_upstream(self):
        with self.assertRaises(urllib.error.HTTPError) as rejected:
            self._post(1025)
        self.assertEqual(rejected.exception.code, 400)
        rejected.exception.close()
        self.assertEqual(_FakeUpstreamHandler.calls, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
