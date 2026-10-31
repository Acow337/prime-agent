#!/usr/bin/env python3
"""Run the bounded DeepSeek smoke experiment in isolated directories."""

from __future__ import annotations

import argparse
import os
import json
import re
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

import tasks
import budget_proxy


PROVIDER = "deepseek"
MODEL = "deepseek-v4-flash"
SMOKE_DIR = Path(__file__).resolve().parent
RESULT_STEM = "2026-10-23"
_SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{20,}")


class SecretDetected(RuntimeError):
    """Raised before credential-shaped text can enter an experiment artifact."""


def read_keychain_key() -> str:
    """Read the live credential from Keychain without exposing captured output."""
    account = os.environ.get("USER")
    if not account:
        raise RuntimeError("Keychain credential unavailable")
    commands = [
        [
            "security",
            "find-generic-password",
            "-a",
            account,
            "-s",
            "prime-agent-deepseek-smoke",
            "-w",
        ],
        [
            "security",
            "find-generic-password",
            "-s",
            "prime-agent-deepseek-smoke",
            "-w",
        ],
    ]
    for command in commands:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        credential = completed.stdout.strip()
        if completed.returncode == 0 and _SECRET_PATTERN.fullmatch(credential):
            return credential
    raise RuntimeError("Keychain credential unavailable")


def build_command(binary: Path, prompt: str) -> list[str]:
    """Build the live headless command without placing credentials in argv."""
    return [
        str(binary),
        "--mode",
        "json",
        "--provider",
        PROVIDER,
        "--model",
        MODEL,
        "-p",
        prompt,
    ]


def isolated_env(root: Path, api_key: str | None = None) -> dict[str, str]:
    """Create an allow-listed process environment rooted below ``root``."""
    root = root.resolve()
    home = root / "home"
    agent = root / "agent"
    sessions = root / "sessions"
    temporary = root / "tmp"
    for path in (home, agent, sessions, temporary):
        path.mkdir(parents=True, exist_ok=True)

    env = {
        name: os.environ[name]
        for name in (
            "PATH",
            "LANG",
            "LC_ALL",
            "TZ",
            "PRIME_AGENT_KERNEL_PYTHON",
            "PA_E2E_KERNEL_PYTHON",
            "PYTHONPATH",
        )
        if name in os.environ
    }
    if "PRIME_AGENT_KERNEL_PYTHON" not in env:
        original_home = os.environ.get("HOME")
        if original_home:
            kernel_python = (
                Path(original_home) / ".prime" / "agent" / "kernel-venv" / "bin" / "python"
            )
            if kernel_python.is_file():
                env["PRIME_AGENT_KERNEL_PYTHON"] = str(kernel_python)
    env.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(root / "config"),
            "XDG_CACHE_HOME": str(root / "cache"),
            "XDG_DATA_HOME": str(root / "data"),
            "TMPDIR": str(temporary),
            "DO_NOT_TRACK": "1",
            "PI_OFFLINE": "1",
            "PI_CODING_AGENT_DIR": str(agent),
            "PRIME_AGENT_CODING_AGENT_DIR": str(agent),
            "PI_SESSION_DIR": str(sessions),
            "PRIME_AGENT_SESSION_DIR": str(sessions),
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
        }
    )
    if api_key is not None:
        env["DEEPSEEK_API_KEY"] = api_key
    return env


def assert_secret_free(value: Any) -> None:
    """Reject values containing a DeepSeek-style credential shape."""
    encoded = json.dumps(value, ensure_ascii=False, default=str)
    if _SECRET_PATTERN.search(encoded):
        raise SecretDetected("credential-shaped value refused")


def safe_json_dumps(value: Any) -> str:
    """Serialize only after the complete value passes the credential scan."""
    assert_secret_free(value)
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _write_live_config(agent_dir: Path, manifest: Mapping[str, Any], base_url: str) -> None:
    models = {
        "providers": {
            PROVIDER: {
                "api": "openai-completions",
                "baseUrl": base_url,
                "models": [
                    {
                        "id": MODEL,
                        "name": "DeepSeek V4 Flash (bounded smoke)",
                        "reasoning": False,
                        "contextWindow": int(manifest["context_tokens"]),
                        "maxTokens": int(manifest["response_tokens"]),
                    }
                ],
            }
        }
    }
    settings = {
        "defaultProvider": PROVIDER,
        "defaultModel": MODEL,
        "prewarmPythonKernel": False,
        "retry": {"enabled": False},
    }
    (agent_dir / "models.json").write_text(
        json.dumps(models, separators=(",", ":")), encoding="utf-8"
    )
    (agent_dir / "settings.json").write_text(
        json.dumps(settings, separators=(",", ":")), encoding="utf-8"
    )


def _assistant_text(stdout: str) -> str:
    answers = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        message = event.get("message")
        if (
            event.get("type") != "message_end"
            or not isinstance(message, dict)
            or message.get("role") != "assistant"
        ):
            continue
        pieces = [
            block.get("text", "")
            for block in message.get("content", [])
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        answers.append("".join(pieces))
    return answers[-1] if answers else ""


def _sanitized_verification(result: Mapping[str, Any]) -> dict[str, Any]:
    allowed = ("success", "score", "reason", "public", "hidden")
    return {key: result[key] for key in allowed if key in result}


def run_cases(
    binary: Path,
    cases: Iterable[tasks.TaskCase],
    runtime_root: Path,
    manifest: Mapping[str, Any],
    *,
    mode: str,
    api_key: str | None = None,
    base_url: str | None = None,
    ledger: budget_proxy.UsageLedger | None = None,
    timeout_seconds: float | None = None,
) -> list[dict[str, Any]]:
    """Run cases sequentially and stop after any unsafe process outcome."""
    if mode not in {"faux", "live"}:
        raise ValueError(f"unsupported execution mode: {mode}")
    if mode == "live" and (api_key is None or base_url is None):
        raise ValueError("live mode requires an API key and proxy base URL")
    timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else float(manifest["task_timeout_seconds"])
    )
    results = []
    for case in cases:
        ledger_before = ledger.summary() if ledger is not None else None
        task_root = runtime_root / case.task_id
        env = isolated_env(task_root, api_key if mode == "live" else None)
        if mode == "live":
            _write_live_config(
                Path(env["PRIME_AGENT_CODING_AGENT_DIR"]), manifest, str(base_url)
            )
        else:
            if case.kind == "json":
                faux_script = {"responses": [{"text": case.reference_output}]}
            elif case.kind == "tests":
                old_delimiter = (
                    '        elif char == "|":\n'
                    "            if current:\n"
                    '                fields.append("".join(current))\n'
                    "                current = []\n"
                )
                new_delimiter = (
                    '        elif char == "|":\n'
                    '            fields.append("".join(current))\n'
                    "            current = []\n"
                )
                old_tail = (
                    "    if current:\n"
                    '        fields.append("".join(current))\n'
                )
                new_tail = (
                    "    if escaped:\n"
                    '        current.append("\\\\")\n'
                    '    fields.append("".join(current))\n'
                )
                python_code = (
                    "from pathlib import Path\n"
                    "path = Path('parser.py')\n"
                    "content = path.read_text(encoding='utf-8')\n"
                    f"content = content.replace({old_delimiter!r}, {new_delimiter!r}, 1)\n"
                    f"content = content.replace({old_tail!r}, {new_tail!r}, 1)\n"
                    "path.write_text(content, encoding='utf-8')\n"
                    "print('parser fixed')\n"
                )
                faux_script = {
                    "responses": [
                        {
                            "content": [
                                {
                                    "type": "toolCall",
                                    "name": "python_repl",
                                    "arguments": {"code": python_code},
                                }
                            ]
                        },
                        {"text": case.reference_output},
                    ]
                }
            else:
                raise ValueError(f"unsupported task kind: {case.kind}")
            env["PRIME_AGENT_FAUX_SCRIPT"] = json.dumps(
                faux_script, separators=(",", ":")
            )
        command = build_command(binary, case.prompt)
        try:
            completed = subprocess.run(
                command,
                cwd=case.workdir,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            results.append({"task_id": case.task_id, "status": "timeout"})
            break

        combined = completed.stdout + completed.stderr
        try:
            assert_secret_free(combined)
            if api_key and api_key in combined:
                raise SecretDetected("exact credential value refused")
        except SecretDetected:
            results.append({"task_id": case.task_id, "status": "secret_rejected"})
            break
        if ledger is not None and ledger_before is not None:
            ledger_after = ledger.summary()
            before_errors = ledger_before["error_categories"]
            after_errors = ledger_after["error_categories"]
            new_errors = {
                category
                for category, count in after_errors.items()
                if count > before_errors.get(category, 0)
            }
            error_statuses = (
                ("missing_usage", "missing_usage"),
                ("invalid_usage", "invalid_usage"),
                ("upstream_http_401", "authentication_error"),
                ("upstream_http_402", "balance_or_budget_error"),
                ("upstream_http_429", "rate_limit_error"),
                ("request_limit", "proxy_rejected"),
                ("budget_limit", "proxy_rejected"),
                ("response_token_limit", "proxy_rejected"),
                ("upstream_connection", "upstream_error"),
            )
            stop_status = next(
                (status for category, status in error_statuses if category in new_errors),
                "proxy_error" if new_errors else None,
            )
            if stop_status is not None:
                results.append({"task_id": case.task_id, "status": stop_status})
                break
        if completed.returncode != 0:
            results.append(
                {
                    "task_id": case.task_id,
                    "status": "process_error",
                    "exit_code": completed.returncode,
                }
            )
            break

        verification = _sanitized_verification(
            tasks.verify(case, _assistant_text(completed.stdout))
        )
        results.append(
            {
                "task_id": case.task_id,
                "status": "completed",
                **verification,
            }
        )
    assert_secret_free(results)
    return results


def _dry_run(cases: Iterable[tasks.TaskCase]) -> list[dict[str, Any]]:
    results = []
    for case in cases:
        if case.kind == "tests":
            tasks.apply_reference_fix(case)
        verification = _sanitized_verification(tasks.verify(case, case.reference_output))
        results.append(
            {"task_id": case.task_id, "status": "verified", **verification}
        )
    return results


def run_experiment(*, mode: str, binary: Path, workspace: Path) -> dict[str, Any]:
    """Run one dry, faux, or live experiment and return a sanitized summary."""
    manifest = json.loads((SMOKE_DIR / "manifest.json").read_text(encoding="utf-8"))
    policy = budget_proxy.BudgetPolicy.from_manifest(manifest)
    if policy.worst_case_request_cny() * policy.max_provider_requests >= policy.hard_budget_cny:
        raise RuntimeError("conservative budget preflight failed")

    fixture_root = workspace / "tasks"
    cases = tasks.materialize_tasks(fixture_root)
    fixture_digest = tasks.fixture_digest(fixture_root)
    ledger = budget_proxy.UsageLedger(policy)
    if mode == "dry-run":
        results = _dry_run(cases)
    elif mode == "faux":
        results = run_cases(
            binary,
            cases,
            workspace / "runs",
            manifest,
            mode="faux",
        )
    elif mode == "live":
        api_key = read_keychain_key()
        proxy = budget_proxy.start_proxy(policy)
        ledger = proxy.ledger
        try:
            results = run_cases(
                binary,
                cases,
                workspace / "runs",
                manifest,
                mode="live",
                api_key=api_key,
                base_url=proxy.base_url,
                ledger=ledger,
            )
        finally:
            proxy.close()
            api_key = ""
    else:
        raise ValueError(f"unsupported experiment mode: {mode}")
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=SMOKE_DIR,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    commit = revision.stdout.strip()
    if revision.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", commit) is None:
        commit = "unknown"
    summary = {
        "schema_version": 1,
        "run_date": date.today().isoformat(),
        "commit": commit,
        "mode": mode,
        "provider": manifest["provider"],
        "model": manifest["model"],
        "fixture_digest": fixture_digest,
        "tasks": results,
        "usage": ledger.summary(),
        "limits": {
            "hard_budget_cny": policy.hard_budget_cny,
            "max_provider_requests": policy.max_provider_requests,
            "response_tokens": policy.response_tokens,
        },
        "scope": "Single-model baseline smoke; no R/S/M treatment was varied.",
    }
    assert_secret_free(summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the bounded DeepSeek baseline smoke experiment."
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--faux", action="store_true")
    modes.add_argument("--live", action="store_true")
    parser.add_argument("--binary", required=True, type=Path)
    arguments = parser.parse_args(argv)

    mode = "dry-run" if arguments.dry_run else "faux" if arguments.faux else "live"
    if mode != "dry-run" and not arguments.binary.is_file():
        parser.error(f"binary not found: {arguments.binary}")

    with tempfile.TemporaryDirectory(prefix="prime-agent-deepseek-smoke-") as directory:
        summary = run_experiment(
            mode=mode,
            binary=arguments.binary.resolve(),
            workspace=Path(directory),
        )

    if mode == "live":
        results_dir = SMOKE_DIR / "results"
        results_dir.mkdir(parents=True, exist_ok=True)
        summary_path = results_dir / f"{RESULT_STEM}-summary.json"
        report_path = results_dir / f"{RESULT_STEM}-report.md"
        summary_path.write_text(safe_json_dumps(summary), encoding="utf-8")
        task_rows = "\n".join(
            f"- `{row['task_id']}`: status `{row['status']}`, "
            f"score `{row.get('score', 0.0)}`, success `{row.get('success', False)}`"
            for row in summary["tasks"]
        )
        usage = summary["usage"]
        report = (
            "# DeepSeek baseline smoke report\n\n"
            f"- Run date: `{summary['run_date']}`\n"
            f"- Commit: `{summary['commit']}`\n"
            f"- Model: `{summary['provider']}/{summary['model']}`\n"
            f"- Task records: `{len(summary['tasks'])}`\n\n"
            "## Results\n\n"
            f"{task_rows}\n\n"
            "## Usage and cost\n\n"
            f"- Provider requests: `{usage['request_count']}`\n"
            f"- Prompt tokens: `{usage['prompt_tokens']}`\n"
            f"- Completion tokens: `{usage['completion_tokens']}`\n"
            f"- Actual cost: `${usage['actual_cost_usd']}` / "
            f"`¥{usage['actual_cost_cny']}`\n"
            f"- Conservative admitted cost: `¥{usage['conservative_cost_cny']}`\n"
            f"- Sanitized error categories: `{json.dumps(usage['error_categories'], sort_keys=True)}`\n\n"
            "## Scope limitation\n\n"
            "This is a single-model baseline smoke test. No R/S/M treatment was varied, "
            "so it does not support a causal harness-ablation claim.\n"
        )
        assert_secret_free(report)
        report_path.write_text(report, encoding="utf-8")

    sys.stdout.write(safe_json_dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
