import json
import http.server
import io
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import redirect_stdout
from unittest import mock
from pathlib import Path


SMOKE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SMOKE_DIR))

import tasks
import budget_proxy
import run_smoke


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
    status_code = 200
    default_response_body = (
        b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":42,'
        b'"completion_tokens":7}}\n\n'
        b"data: [DONE]\n\n"
    )
    response_body = default_response_body

    def do_POST(self):
        type(self).calls += 1
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(type(self).status_code)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, *_args):
        pass


class ProxyTests(unittest.TestCase):
    def setUp(self):
        _FakeUpstreamHandler.calls = 0
        _FakeUpstreamHandler.status_code = 200
        _FakeUpstreamHandler.response_body = _FakeUpstreamHandler.default_response_body
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

    def test_successful_stream_without_usage_is_recorded_as_an_error(self):
        _FakeUpstreamHandler.response_body = (
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        with self._post(1024) as response:
            response.read()
        self.assertEqual(
            self.proxy.ledger.summary()["error_categories"], {"missing_usage": 1}
        )


class RunnerTests(unittest.TestCase):
    def _executable(self, directory, source):
        path = Path(directory) / "synthetic-agent"
        path.write_text("#!/usr/bin/env python3\n" + source, encoding="utf-8")
        path.chmod(0o700)
        return path

    def test_command_and_environment_are_isolated(self):
        command = run_smoke.build_command(Path("/bin/prime-agent"), "prompt")
        self.assertIn("json", command)
        self.assertNotIn("secret", " ".join(command))
        with tempfile.TemporaryDirectory() as directory:
            env = run_smoke.isolated_env(Path(directory), "secret")
            self.assertEqual(env["DEEPSEEK_API_KEY"], "secret")
            self.assertEqual(env["PI_OFFLINE"], "1")
            self.assertTrue(Path(env["PRIME_AGENT_CODING_AGENT_DIR"]).is_absolute())

    def test_keychain_read_captures_the_key_without_placing_it_in_argv(self):
        credential = "s" + "k-" + ("x" * 24)
        completed = subprocess.CompletedProcess([], 0, stdout=credential + "\n", stderr="")
        with mock.patch.object(run_smoke.subprocess, "run", return_value=completed) as run:
            self.assertEqual(run_smoke.read_keychain_key(), credential)
        command = run.call_args.args[0]
        self.assertNotIn(credential, " ".join(command))
        self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_keychain_failure_does_not_include_captured_output(self):
        completed = subprocess.CompletedProcess(
            [], 44, stdout="sensitive-stdout", stderr="sensitive-stderr"
        )
        with mock.patch.object(run_smoke.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(RuntimeError, "Keychain credential unavailable") as error:
                run_smoke.read_keychain_key()
        self.assertNotIn("sensitive", str(error.exception))

    def test_timeout_stops_before_later_tasks(self):
        manifest = json.loads((SMOKE_DIR / "manifest.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = self._executable(
                root,
                "import time\ntime.sleep(2)\n",
            )
            cases = tasks.materialize_tasks(root / "tasks")
            results = run_smoke.run_cases(
                binary,
                cases,
                root / "runs",
                manifest,
                mode="live",
                api_key="credential",
                base_url="http://127.0.0.1:1",
                timeout_seconds=0.05,
            )
            self.assertEqual([row["status"] for row in results], ["timeout"])

    def test_nonzero_exit_is_classified_without_raw_stderr(self):
        manifest = json.loads((SMOKE_DIR / "manifest.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = self._executable(
                root,
                "import sys\nsys.stderr.write('do-not-persist')\nsys.exit(7)\n",
            )
            case = tasks.materialize_tasks(root / "tasks")[0]
            results = run_smoke.run_cases(
                binary,
                [case],
                root / "runs",
                manifest,
                mode="live",
                api_key="credential",
                base_url="http://127.0.0.1:1",
            )
            serialized = run_smoke.safe_json_dumps(results)
            self.assertEqual(results[0]["status"], "process_error")
            self.assertNotIn("do-not-persist", serialized)

    def test_secret_shaped_output_is_rejected_before_serialization(self):
        manifest = json.loads((SMOKE_DIR / "manifest.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = self._executable(
                root,
                "import json\n"
                "value = 's' + 'k-' + ('x' * 24)\n"
                "print(json.dumps({'type': 'message_end', 'message': "
                "{'role': 'assistant', 'content': [{'type': 'text', 'text': value}]}}))\n",
            )
            case = tasks.materialize_tasks(root / "tasks")[0]
            results = run_smoke.run_cases(
                binary,
                [case],
                root / "runs",
                manifest,
                mode="live",
                api_key="credential",
                base_url="http://127.0.0.1:1",
            )
            self.assertEqual(results[0]["status"], "secret_rejected")
            run_smoke.safe_json_dumps(results)

    def test_non_object_ndjson_events_are_ignored(self):
        manifest = json.loads((SMOKE_DIR / "manifest.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            case = tasks.materialize_tasks(root / "tasks")[0]
            binary = self._executable(
                root,
                "import json\n"
                "print('[]')\n"
                f"answer = {case.reference_output!r}\n"
                "print(json.dumps({'type': 'message_end', 'message': "
                "{'role': 'assistant', 'content': [{'type': 'text', 'text': answer}]}}))\n",
            )
            results = run_smoke.run_cases(
                binary,
                [case],
                root / "runs",
                manifest,
                mode="faux",
            )
        self.assertTrue(results[0]["success"])

    def test_live_run_stops_when_a_successful_stream_omits_usage(self):
        manifest = json.loads((SMOKE_DIR / "manifest.json").read_text())
        _FakeUpstreamHandler.calls = 0
        _FakeUpstreamHandler.response_body = (
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            b"data: [DONE]\n\n"
        )
        upstream = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _FakeUpstreamHandler
        )
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        policy = budget_proxy.BudgetPolicy.from_manifest(manifest)
        proxy = budget_proxy.start_proxy(
            policy,
            upstream=f"http://127.0.0.1:{upstream.server_port}/chat/completions",
        )
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                cases = tasks.materialize_tasks(root / "tasks")
                binary = self._executable(
                    root,
                    "import json, os, urllib.request\n"
                    "agent = os.environ['PRIME_AGENT_CODING_AGENT_DIR']\n"
                    "with open(agent + '/models.json', encoding='utf-8') as stream:\n"
                    "    base = json.load(stream)['providers']['deepseek']['baseUrl']\n"
                    "request = urllib.request.Request(base + '/chat/completions', "
                    "data=json.dumps({'max_tokens': 1024, 'stream': True}).encode(), "
                    "headers={'Authorization': 'Bearer ' + os.environ['DEEPSEEK_API_KEY'], "
                    "'Content-Type': 'application/json'}, method='POST')\n"
                    "with urllib.request.urlopen(request) as response:\n"
                    "    response.read()\n"
                    f"answer = {cases[0].reference_output!r}\n"
                    "print(json.dumps({'type': 'message_end', 'message': "
                    "{'role': 'assistant', 'content': [{'type': 'text', 'text': answer}]}}))\n",
                )
                results = run_smoke.run_cases(
                    binary,
                    cases,
                    root / "runs",
                    manifest,
                    mode="live",
                    api_key="credential",
                    base_url=proxy.base_url,
                    ledger=proxy.ledger,
                )
        finally:
            proxy.close()
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join()
            _FakeUpstreamHandler.response_body = (
                _FakeUpstreamHandler.default_response_body
            )
        self.assertEqual([row["status"] for row in results], ["missing_usage"])

    def test_live_http_failures_are_sanitized_and_not_retried(self):
        manifest = json.loads((SMOKE_DIR / "manifest.json").read_text())
        expected = {
            401: "authentication_error",
            402: "balance_or_budget_error",
            429: "rate_limit_error",
        }
        for status_code, expected_status in expected.items():
            with self.subTest(status_code=status_code):
                _FakeUpstreamHandler.calls = 0
                _FakeUpstreamHandler.status_code = status_code
                _FakeUpstreamHandler.response_body = b'{"error":{"message":"private"}}'
                upstream = http.server.ThreadingHTTPServer(
                    ("127.0.0.1", 0), _FakeUpstreamHandler
                )
                upstream_thread = threading.Thread(
                    target=upstream.serve_forever, daemon=True
                )
                upstream_thread.start()
                policy = budget_proxy.BudgetPolicy.from_manifest(manifest)
                proxy = budget_proxy.start_proxy(
                    policy,
                    upstream=(
                        f"http://127.0.0.1:{upstream.server_port}/chat/completions"
                    ),
                )
                try:
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        case = tasks.materialize_tasks(root / "tasks")[0]
                        binary = self._executable(
                            root,
                            "import json, os, pathlib, urllib.request\n"
                            "agent = pathlib.Path(os.environ['PRIME_AGENT_CODING_AGENT_DIR'])\n"
                            "base = json.loads((agent / 'models.json').read_text())"
                            "['providers']['deepseek']['baseUrl']\n"
                            "request = urllib.request.Request(base + '/chat/completions', "
                            "data=json.dumps({'max_tokens': 1024, 'stream': True}).encode(), "
                            "headers={'Authorization': 'Bearer credential', "
                            "'Content-Type': 'application/json'}, method='POST')\n"
                            "urllib.request.urlopen(request).read()\n",
                        )
                        results = run_smoke.run_cases(
                            binary,
                            [case],
                            root / "runs",
                            manifest,
                            mode="live",
                            api_key="credential",
                            base_url=proxy.base_url,
                            ledger=proxy.ledger,
                        )
                finally:
                    proxy.close()
                    upstream.shutdown()
                    upstream.server_close()
                    upstream_thread.join()
                self.assertEqual(results, [{"task_id": case.task_id, "status": expected_status}])
                self.assertEqual(_FakeUpstreamHandler.calls, 1)
        _FakeUpstreamHandler.status_code = 200
        _FakeUpstreamHandler.response_body = _FakeUpstreamHandler.default_response_body

    def test_dry_run_verifies_all_tasks_without_a_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary = run_smoke.run_experiment(
                mode="dry-run",
                binary=root / "does-not-exist",
                workspace=root / "workspace",
            )
            tasks.materialize_tasks(root / "fresh-tasks")
            expected_digest = tasks.fixture_digest(root / "fresh-tasks")
        self.assertEqual(summary["mode"], "dry-run")
        self.assertEqual(len(summary["tasks"]), 3)
        self.assertTrue(all(row["success"] for row in summary["tasks"]), summary)
        self.assertEqual(summary["fixture_digest"], expected_digest)
        self.assertEqual(summary["usage"]["request_count"], 0)
        self.assertEqual(summary["usage"]["actual_cost_cny"], 0.0)

    def test_faux_run_verifies_all_tasks_without_credentials_or_usage(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = self._executable(
                root,
                "import json, os, pathlib, sys\n"
                "if 'DEEPSEEK_API_KEY' in os.environ:\n"
                "    sys.exit(9)\n"
                "script = json.loads(os.environ['PRIME_AGENT_FAUX_SCRIPT'])\n"
                "for response in script['responses']:\n"
                "    for block in response.get('content', []):\n"
                "        if block.get('type') != 'toolCall':\n"
                "            continue\n"
                "        if block.get('name') != 'python_repl':\n"
                "            sys.exit(8)\n"
                "        exec(block['arguments']['code'])\n"
                "answer = script['responses'][-1].get('text', '')\n"
                "print(json.dumps({'type': 'message_end', 'message': "
                "{'role': 'assistant', 'content': [{'type': 'text', 'text': answer}]}}))\n",
            )
            summary = run_smoke.run_experiment(
                mode="faux",
                binary=binary,
                workspace=root / "workspace",
            )
        self.assertEqual(summary["mode"], "faux")
        self.assertEqual(len(summary["tasks"]), 3)
        self.assertTrue(all(row["success"] for row in summary["tasks"]))
        self.assertEqual(summary["usage"]["request_count"], 0)
        self.assertEqual(summary["usage"]["actual_cost_cny"], 0.0)

    def test_live_run_uses_the_bounded_proxy_and_returns_sanitized_usage(self):
        _FakeUpstreamHandler.calls = 0
        _FakeUpstreamHandler.response_body = _FakeUpstreamHandler.default_response_body
        upstream = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), _FakeUpstreamHandler
        )
        upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
        upstream_thread.start()
        upstream_url = (
            f"http://127.0.0.1:{upstream.server_port}/chat/completions"
        )
        real_start_proxy = budget_proxy.start_proxy
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                binary = self._executable(
                    root,
                    "import json, os, pathlib, urllib.request\n"
                    "cwd = pathlib.Path.cwd()\n"
                    "if cwd.name == 't1-structured':\n"
                    "    answer = json.dumps({'successful_units': 31, "
                    "'alpha_successful_units': 17, 'successes_before_10': 2, "
                    "'highest_success_team': 'alpha'})\n"
                    "elif cwd.name == 't2-log-correlation':\n"
                    "    answer = json.dumps({'canonical_account': 'acct-17', "
                    "'root_cause': 'expired_lease', 'terminal_status': 'recovered'})\n"
                    "else:\n"
                    "    path = cwd / 'parser.py'\n"
                    "    content = path.read_text()\n"
                    "    content = content.replace('''        elif char == \"|\":\n"
                    "            if current:\n"
                    "                fields.append(\"\".join(current))\n"
                    "                current = []\n''', '''        elif char == \"|\":\n"
                    "            fields.append(\"\".join(current))\n"
                    "            current = []\n''')\n"
                    "    content = content.replace('''    if current:\n"
                    "        fields.append(\"\".join(current))\n''', "
                    "'    if escaped:\\n        current.append(' + repr(chr(92)) + "
                    "')\\n    fields.append(\"\".join(current))\\n')\n"
                    "    path.write_text(content)\n"
                    "    answer = 'fixed'\n"
                    "agent = pathlib.Path(os.environ['PRIME_AGENT_CODING_AGENT_DIR'])\n"
                    "base = json.loads((agent / 'models.json').read_text())"
                    "['providers']['deepseek']['baseUrl']\n"
                    "request = urllib.request.Request(base + '/chat/completions', "
                    "data=json.dumps({'max_tokens': 1024, 'stream': True}).encode(), "
                    "headers={'Authorization': 'Bearer ' + os.environ['DEEPSEEK_API_KEY'], "
                    "'Content-Type': 'application/json'}, method='POST')\n"
                    "with urllib.request.urlopen(request) as response:\n"
                    "    response.read()\n"
                    "print(json.dumps({'type': 'message_end', 'message': "
                    "{'role': 'assistant', 'content': [{'type': 'text', 'text': answer}]}}))\n",
                )
                with mock.patch.object(
                    run_smoke,
                    "read_keychain_key",
                    return_value="credential",
                ), mock.patch.object(
                    run_smoke.budget_proxy,
                    "start_proxy",
                    side_effect=lambda policy: real_start_proxy(
                        policy, upstream=upstream_url
                    ),
                ):
                    summary = run_smoke.run_experiment(
                        mode="live",
                        binary=binary,
                        workspace=root / "workspace",
                    )
        finally:
            upstream.shutdown()
            upstream.server_close()
            upstream_thread.join()
        self.assertEqual(summary["mode"], "live")
        self.assertEqual(len(summary["tasks"]), 3)
        self.assertTrue(all(row["success"] for row in summary["tasks"]), summary)
        self.assertEqual(summary["usage"]["request_count"], 3)
        self.assertEqual(summary["usage"]["error_categories"], {})

    def test_dry_run_cli_prints_safe_json_without_requiring_the_binary(self):
        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = run_smoke.main(
                ["--dry-run", "--binary", "/path/that/does/not/exist"]
            )
        summary = json.loads(output.getvalue())
        self.assertEqual(exit_code, 0)
        self.assertEqual(summary["mode"], "dry-run")
        self.assertEqual(len(summary["tasks"]), 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
