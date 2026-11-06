"""Offline tests for the ablation matrix runner and the I-family tasks."""

from __future__ import annotations

import http.server
import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE / "families"))

import e_family  # noqa: E402
import t_family  # noqa: E402
import t_mech_family  # noqa: E402
import i_family  # noqa: E402
import runner  # noqa: E402
import freeze  # noqa: E402

# What an OpenRouter run pins on every request: one provider endpoint with no
# fallbacks, and the reasoning mode.
_PINNED = {
    "provider": {"order": ["deepinfra/fp8"], "allow_fallbacks": False},
    "reasoning": {"effort": "none"},
}
# An OpenRouter stream: every chunk names the model and provider that served
# it, and the final chunk carries the usage with the billed cost.
_OPENROUTER_STREAM = (
    b'data: {"model":"deepseek/deepseek-v4-flash-20260423","provider":"DeepInfra",'
    b'"choices":[{"delta":{"content":"ok"}}]}\n\n'
    b'data: {"model":"deepseek/deepseek-v4-flash-20260423","provider":"DeepInfra",'
    b'"choices":[{"delta":{},"finish_reason":"stop"}],'
    b'"usage":{"prompt_tokens":42,"completion_tokens":7,"cost":0.0000125}}\n\n'
    b"data: [DONE]\n\n"
)


class IFamilyTests(unittest.TestCase):
    def test_materialization_is_deterministic_and_answers_verify(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            left = i_family.materialize(1, Path(first))
            right = i_family.materialize(1, Path(second))
            for name in ("orders.csv", "customers.csv", "refunds.csv"):
                self.assertEqual(
                    (left.workdir / name).read_bytes(),
                    (right.workdir / name).read_bytes(),
                )
            self.assertEqual(left.expected, right.expected)
            self.assertTrue(i_family.verify(left, left.reference)["success"])
            self.assertEqual(i_family.verify(left, left.reference)["score"], 1.0)

    def test_wrong_and_unparseable_answers_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            case = i_family.materialize(2, Path(directory))
        self.assertEqual(i_family.verify(case, "not json")["score"], 0.0)
        wrong = dict(case.expected, revenue_cents=case.expected["revenue_cents"] + 10)
        result = i_family.verify(case, json.dumps(wrong))
        self.assertFalse(result["success"])
        self.assertAlmostEqual(result["score"], 5 / 6)

    def test_instances_differ(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            one = i_family.materialize(1, root)
            two = i_family.materialize(2, root)
        self.assertNotEqual(one.expected, two.expected)


class EFamilyTests(unittest.TestCase):
    def test_buggy_repo_fails_and_reference_fix_turns_it_green(self):
        with tempfile.TemporaryDirectory() as directory:
            case = e_family.materialize(1, Path(directory))
            self.assertEqual(e_family.verify(case, "")["score"], 0.0)
            self.assertTrue(e_family.dry_run_verify(case)["success"])

    def test_instances_vary_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = (e_family.materialize(1, root).workdir / "config.py").read_text()
            second = (e_family.materialize(4, root).workdir / "config.py").read_text()
        self.assertNotEqual(first, second)


class TFamilyTests(unittest.TestCase):
    def test_series_has_three_episodes_with_one_learning_episode(self):
        with tempfile.TemporaryDirectory() as directory:
            case = t_family.materialize(1, Path(directory))
        self.assertEqual([episode.name for episode in case.episodes], ["ep1", "ep2", "ep3"])
        self.assertEqual([episode.scored for episode in case.episodes], [False, True, True])
        self.assertNotIn("error_rate_pct >= 5", case.episodes[1].prompt)
        self.assertNotIn("Triage rule:", case.episodes[1].prompt)

    def test_reference_answers_score_one_and_wrong_labels_score_lower(self):
        with tempfile.TemporaryDirectory() as directory:
            case = t_family.materialize(1, Path(directory))
        for index, episode in enumerate(case.episodes):
            self.assertTrue(t_family.verify_episode(case, index, episode.reference)["success"])
        self.assertTrue(t_family.dry_run_verify(case)["success"])
        wrong = {"items": {}}
        self.assertEqual(t_family.verify_episode(case, 1, json.dumps(wrong))["score"], 0.0)

    def test_episode_three_uses_the_changed_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            case = t_family.materialize(1, Path(directory))
        import json as _json

        old = _json.loads(case.episodes[1].reference)["items"]
        new = _json.loads(case.episodes[2].reference)["items"]
        self.assertNotEqual(old, new)


class MechFamilyTests(unittest.TestCase):
    def test_thresholds_are_arbitrary_and_instance_specific(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = t_mech_family.materialize(1, root)
            second = t_mech_family.materialize(2, root)
            first_rule = first.episodes[0].prompt
            second_rule = second.episodes[0].prompt
        first_thresholds = t_mech_family._thresholds(1)
        second_thresholds = t_mech_family._thresholds(2)
        self.assertIn(str(first_thresholds.high_error), first_rule)
        self.assertIn(str(first_thresholds.high_latency), first_rule)
        self.assertNotIn(str(first_thresholds.high_error), second_rule)
        self.assertNotIn(str(second_thresholds.high_error), first_rule)
        self.assertNotIn("800", first_rule)

    def test_reference_answers_verify_and_episodes_are_scored_like_t(self):
        with tempfile.TemporaryDirectory() as directory:
            case = t_mech_family.materialize(1, Path(directory))
        self.assertEqual([e.scored for e in case.episodes], [False, True, True])
        for index, episode in enumerate(case.episodes):
            self.assertTrue(t_mech_family.verify_episode(case, index, episode.reference)["success"])
        self.assertFalse(any(str(t_mech_family._thresholds(1).high_error) in e.prompt for e in case.episodes[1:]))
        self.assertTrue(t_mech_family.dry_run_verify(case)["success"])


class MatrixRunnerTests(unittest.TestCase):
    def _config(self) -> dict:
        config = json.loads((HERE / "config.pilot.json").read_text(encoding="utf-8"))
        config["families"] = {"i": [1, 2]}
        config["mode"] = "dry-run"
        return config

    def test_dry_run_walks_every_cell_and_summarizes(self):
        config = self._config()
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / "results"
            records = runner.run_matrix(
                config, Path("unused-binary"), results, "dry-run"
            )
            self.assertEqual(len(records), 2 * len(runner.TREATMENTS))
            self.assertEqual(len({record["run_id"] for record in records}), len(records))
            self.assertTrue(all(record["status"] == "completed" for record in records))
            self.assertTrue(all(record["success"] for record in records))
            summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["counts"]["completed"], len(records))
            self.assertEqual(set(summary["treatments"]), set(runner.TREATMENTS))
            for treatment in summary["treatments"].values():
                self.assertEqual(treatment["success_rate"], 1.0)
            self.assertEqual(summary["usage"]["actual_cost_cny"], 0.0)

    def test_the_summary_merges_billed_cost_and_provenance_across_cells(self):
        def cell(run_id, usage):
            return {
                "run_id": run_id,
                "treatment": "r1-s1-m1",
                "status": "completed",
                "success": True,
                "score": 1.0,
                "usage": usage,
            }

        served = "deepseek/deepseek-v4-flash-20260423 @ DeepInfra"
        records = [
            cell(
                "a",
                {
                    "request_count": 2,
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "actual_cost_cny": 0.0002,
                    "reported_cost_usd": 0.000025,
                    "served_by": {served: 2},
                },
            ),
            cell(
                "b",
                {
                    "request_count": 1,
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "actual_cost_cny": 0.0001,
                    "reported_cost_usd": 0.0000125,
                    "served_by": {served: 1},
                },
            ),
            # Written before the ledger reported billed cost or provenance.
            cell(
                "c",
                {
                    "request_count": 1,
                    "prompt_tokens": 5,
                    "completion_tokens": 1,
                    "actual_cost_cny": 0.00001,
                },
            ),
        ]
        self.assertEqual(
            runner.summarize(records, self._config())["usage"],
            {
                "prompt_tokens": 25,
                "completion_tokens": 5,
                "actual_cost_cny": 0.00031,
                "reported_cost_usd": 0.0000375,
                "served_by": {served: 3},
            },
        )

    def test_resume_skips_completed_cells(self):
        config = self._config()
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / "results"
            first = runner.run_matrix(config, Path("unused"), results, "dry-run")
            second = runner.run_matrix(config, Path("unused"), results, "dry-run")
            self.assertEqual(len(first), 16)
            self.assertEqual(second, [])
            lines = (results / "records.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 16)

    def test_resumed_run_summarizes_every_recorded_cell(self):
        config = self._config()
        with tempfile.TemporaryDirectory() as directory:
            results = Path(directory) / "results"
            runner.run_matrix(config, Path("unused"), results, "dry-run", limit=3)
            runner.run_matrix(config, Path("unused"), results, "dry-run")
            summary = json.loads((results / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(
                summary["counts"],
                {"completed": 16, "budget_exhausted": 0, "failed": 0, "total": 16},
            )

    def test_planned_matrix_covers_models_treatments_and_instances(self):
        config = self._config()
        config["models"] = {"cheap": "deepseek-v4-flash", "strong": "deepseek-v4-pro"}
        runs = runner.planned_runs(config)
        self.assertEqual(len(runs), 2 * 2 * len(runner.TREATMENTS))
        self.assertEqual(
            {run["treatment"] for run in runs}, set(runner.TREATMENTS)
        )
        self.assertEqual({run["task_id"] for run in runs}, {"i-01", "i-02"})


class FailureAccountingTests(unittest.TestCase):
    def test_each_cell_counts_its_first_completed_or_budget_exhausted_attempt(self):
        def attempt(run_id: str, status: str, requests: int, **errors: dict) -> dict:
            return {"run_id": run_id, "status": status, "usage": {"request_count": requests}, **errors}

        records = [
            # Written before the runner classified budget exhaustion: the
            # exhausted quota is the agent's failure, so the retry is ignored.
            attempt("a", "infra_error", 40, error_categories={"task_request_limit": 1}),
            attempt("a", "completed", 9),
            # Old-format transport failure below the quota: retried.
            attempt("b", "infra_error", 12, error_categories={"upstream_connection": 3}),
            attempt("b", "completed", 10),
            attempt("c", "infra_error", 40, cell_error_categories={"upstream_connection": 1}),
            attempt("c", "completed", 11),
            # Out of a smaller quota the run later raised: superseded.
            attempt("d", "budget_exhausted", 8, cell_error_categories={"task_request_limit": 1}),
            attempt("d", "completed", 12),
            attempt("e", "timeout", 5),
            attempt("e", "completed", 7),
            attempt("f", "budget_exhausted", 40, cell_error_categories={"task_request_limit": 2}),
            attempt(
                "g",
                "infra_error",
                40,
                cell_error_categories={"task_request_limit": 1, "upstream_connection": 1},
            ),
        ]
        self.assertEqual(
            runner.counted_records(records, quota=40),
            [records[0], records[3], records[5], records[7], records[9], records[10]],
        )


class _ProviderHandler(http.server.BaseHTTPRequestHandler):
    """Answers every chat request with `body`, a stream ending in its usage.

    Records, per request, the credential it arrived with and the key the agent
    reported holding (a reporting agent puts it in the body), and the body as
    the proxy sent it.
    """

    seen: list[dict[str, str | None]] = []
    payloads: list[dict] = []
    default_body = (
        b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":42,"completion_tokens":7}}\n\n'
        b"data: [DONE]\n\n"
    )
    body = default_body

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        type(self).payloads.append(payload)
        type(self).seen.append(
            {
                "authorization": self.headers.get("Authorization"),
                "agent_key": payload.get("agent_key"),
            }
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(type(self).body)

    def log_message(self, *_args):
        pass


# Sends three requests through the runner's proxy, one more than the test quota.
_THREE_REQUEST_AGENT = """#!/usr/bin/env python3
import json, os, pathlib, urllib.error, urllib.request
agent = pathlib.Path(os.environ["PRIME_AGENT_CODING_AGENT_DIR"])
provider = json.loads((agent / "settings.json").read_text())["defaultProvider"]
base = json.loads((agent / "models.json").read_text())["providers"][provider]["baseUrl"]
for _ in range(3):
    request = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps({"max_tokens": 64, "stream": True}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            response.read()
    except urllib.error.HTTPError as error:
        error.read()
print(json.dumps({"type": "message_end", "message": {"role": "assistant",
    "content": [{"type": "text", "text": "{}"}]}}))
"""


# Authenticates with the key the runner gave it, under its provider's key
# variable, and reports that key in the body.
_KEY_REPORTING_AGENT = """#!/usr/bin/env python3
import json, os, pathlib, urllib.request
agent = pathlib.Path(os.environ["PRIME_AGENT_CODING_AGENT_DIR"])
provider = json.loads((agent / "settings.json").read_text())["defaultProvider"]
base = json.loads((agent / "models.json").read_text())["providers"][provider]["baseUrl"]
key = os.environ[{"deepseek": "DEEPSEEK_API_KEY", "openrouter": "OPENROUTER_API_KEY"}[provider]]
request = urllib.request.Request(
    base + "/chat/completions",
    data=json.dumps({"max_tokens": 64, "stream": True, "agent_key": key}).encode(),
    headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(request) as response:
    response.read()
print(json.dumps({"type": "message_end", "message": {"role": "assistant",
    "content": [{"type": "text", "text": "{}"}]}}))
"""


class LiveAccountingTests(unittest.TestCase):
    def setUp(self):
        _ProviderHandler.seen = []
        _ProviderHandler.payloads = []
        _ProviderHandler.body = _ProviderHandler.default_body
        self.upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ProviderHandler)
        self.upstream_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self.upstream_thread.start()

    def tearDown(self):
        self.upstream.shutdown()
        self.upstream.server_close()
        self.upstream_thread.join()

    def _run_live(self, agent_source, *, invocations, provider="deepseek", request_fields=None):
        """Run one live I cell for `provider` (with `request_fields` in its
        config, if given) `invocations` times against the fake provider, with
        Keychain returning "credential" for whichever provider is asked; keep the
        credentials the proxy was started with in `self.proxy_credentials` and
        return the cell's records."""
        upstream_url = f"http://127.0.0.1:{self.upstream.server_port}/chat/completions"
        real_start_proxy = runner.budget_proxy.start_proxy
        config = json.loads((HERE / "config.pilot.json").read_text(encoding="utf-8"))
        config.update(
            families={"i": [1]},
            treatments=["r1-s1-m1"],
            max_requests_per_run=2,
            mode="live",
            provider=provider,
        )
        if request_fields is not None:
            config["request_fields"] = request_fields
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            runner.run_smoke,
            "read_keychain_key",
            side_effect=lambda name: runner.budget_proxy.ProviderCredential(name, "credential"),
        ), mock.patch.object(
            runner.budget_proxy,
            "start_proxy",
            side_effect=lambda policy, credential, request_fields: real_start_proxy(
                policy, credential=credential, request_fields=request_fields, upstream=upstream_url
            ),
        ) as start_proxy:
            agent = Path(directory) / "synthetic-agent"
            agent.write_text(agent_source, encoding="utf-8")
            agent.chmod(0o700)
            results = Path(directory) / "results"
            for _ in range(invocations):
                runner.run_matrix(config, agent, results, "live")
            self.proxy_credentials = [
                call.kwargs["credential"] for call in start_proxy.call_args_list
            ]
            return [
                json.loads(line)
                for line in (results / "records.jsonl").read_text(encoding="utf-8").splitlines()
            ]

    def test_a_cell_out_of_request_budget_is_counted_and_not_retried(self):
        records = self._run_live(_THREE_REQUEST_AGENT, invocations=2)
        self.assertEqual(
            [
                {
                    "status": record["status"],
                    "errors": record.get("cell_error_categories"),
                    "requests": record["usage"]["request_count"],
                }
                for record in records
            ],
            [{"status": "budget_exhausted", "errors": {"task_request_limit": 1}, "requests": 2}],
        )

    def test_only_the_proxy_holds_the_configured_providers_credential(self):
        self._run_live(
            _KEY_REPORTING_AGENT, invocations=1, provider="openrouter", request_fields=_PINNED
        )
        self.assertEqual(
            (self.proxy_credentials, _ProviderHandler.seen),
            (
                [runner.budget_proxy.ProviderCredential("openrouter", "credential")],
                [
                    {
                        "authorization": "Bearer credential",
                        "agent_key": runner.run_smoke.PROXY_CREDENTIAL_PLACEHOLDER,
                    }
                ],
            ),
        )


    def test_an_openrouter_cell_is_pinned_and_records_its_billed_cost_and_provenance(self):
        _ProviderHandler.body = _OPENROUTER_STREAM
        records = self._run_live(
            _KEY_REPORTING_AGENT, invocations=1, provider="openrouter", request_fields=_PINNED
        )
        self.assertEqual(
            (
                [{key: payload.get(key) for key in _PINNED} for payload in _ProviderHandler.payloads],
                records[0]["usage"],
            ),
            (
                [_PINNED],
                {
                    "request_count": 1,
                    "prompt_tokens": 42,
                    "completion_tokens": 7,
                    "actual_cost_cny": 0.0001,
                    "reported_cost_usd": 0.0000125,
                    "served_by": {"deepseek/deepseek-v4-flash-20260423 @ DeepInfra": 1},
                },
            ),
        )


# The OpenRouter endpoint listing for one model, as GET /models/{id}/endpoints
# returns it (trimmed to the fields the snapshot keeps).
_ENDPOINTS = {
    "data": {
        "id": "deepseek/deepseek-v4-flash",
        "endpoints": [
            {
                "name": "DeepInfra | deepseek/deepseek-v4-flash-20260423",
                "provider_name": "DeepInfra",
                "tag": "deepinfra/fp8",
                "quantization": "fp8",
                "context_length": 1048576,
                "max_completion_tokens": 393216,
                "pricing": {"prompt": "0.00000009", "completion": "0.00000018", "discount": 0},
                "supported_parameters": ["max_tokens", "reasoning", "tools"],
            },
            {
                "name": "Venice | deepseek/deepseek-v4-flash-20260423",
                "provider_name": "Venice",
                "tag": "venice",
                "quantization": "unknown",
                "context_length": 1000000,
                "max_completion_tokens": 262144,
                "pricing": {"prompt": "0.0000000966", "completion": "0.0000001925", "discount": 0.3},
                "supported_parameters": ["max_tokens", "reasoning", "tools"],
            },
        ],
    }
}


class FreezeTests(unittest.TestCase):
    def _freeze(self, listing=_ENDPOINTS, **prices):
        """Freeze an OpenRouter pilot config pinned to deepinfra/fp8 with `prices`,
        against `listing`; return the URL fetched and the frozen file."""
        config = json.loads((HERE / "config.pilot.json").read_text(encoding="utf-8"))
        config.update(
            provider="openrouter",
            models={"cheap": "deepseek/deepseek-v4-flash"},
            request_fields=_PINNED,
            families={"i": [1]},
            **prices,
        )
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            freeze.urllib.request,
            "urlopen",
            return_value=io.BytesIO(json.dumps(listing).encode()),
        ) as urlopen, redirect_stdout(io.StringIO()):
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            out = Path(directory) / "frozen.json"
            freeze.main(["--config", str(config_path), "--out", str(out)])
            return urlopen.call_args.args[0], json.loads(out.read_text(encoding="utf-8"))

    def test_an_openrouter_freeze_snapshots_the_pinned_endpoint(self):
        url, frozen = self._freeze(input_usd_per_million=0.09, output_usd_per_million=0.18)
        self.assertEqual(
            (url, {key: frozen[key] for key in ("provider", "request_fields", "prices")}),
            (
                "https://openrouter.ai/api/v1/models/deepseek/deepseek-v4-flash/endpoints",
                {
                    "provider": "openrouter",
                    "request_fields": _PINNED,
                    "prices": {
                        "cheap": {
                            "model": "deepseek/deepseek-v4-flash",
                            "endpoint": "DeepInfra | deepseek/deepseek-v4-flash-20260423",
                            "tag": "deepinfra/fp8",
                            "provider_name": "DeepInfra",
                            "quantization": "fp8",
                            "context_length": 1048576,
                            "max_completion_tokens": 393216,
                            "pricing": {
                                "prompt": "0.00000009",
                                "completion": "0.00000018",
                                "discount": 0,
                            },
                            "supported_parameters": ["max_tokens", "reasoning", "tools"],
                        }
                    },
                },
            ),
        )

    def test_config_prices_below_the_pinned_endpoints_listing_are_refused(self):
        with self.assertRaisesRegex(SystemExit, "below the highest listed prompt price"):
            self._freeze(input_usd_per_million=0.05, output_usd_per_million=0.18)

    def test_a_time_of_day_override_sets_the_price_floor(self):
        # DeepSeek's own endpoint charges double in weekday peak hours.
        listing = json.loads(json.dumps(_ENDPOINTS))
        pricing = listing["data"]["endpoints"][0]["pricing"]
        pricing["overrides"] = [
            {
                "utc_start": 100,
                "utc_end": 400,
                "utc_days": ["monday"],
                "prompt": "0.00000018",
                "completion": "0.00000036",
            }
        ]
        with self.assertRaisesRegex(SystemExit, "below the highest listed prompt price"):
            self._freeze(listing, input_usd_per_million=0.09, output_usd_per_million=0.18)
        _url, frozen = self._freeze(
            listing, input_usd_per_million=0.18, output_usd_per_million=0.36
        )
        self.assertEqual(frozen["prices"]["cheap"]["pricing"], pricing)


if __name__ == "__main__":
    unittest.main()
