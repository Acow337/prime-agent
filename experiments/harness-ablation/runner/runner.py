"""Matrix runner for the harness-ablation pilot.

Runs every (model, R/S/M treatment, task instance) cell, verifies the model's
answer with the task family's verifier, and writes one JSONL record per cell so
an interrupted run resumes without repeating completed cells.

Live runs go through the bounded loopback proxy from the smoke harness: one
global cost gate, a per-run request quota, and transport-level retries. Nothing
outside the results directory and a temporary workspace is written.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SMOKE = HERE.parent / "smoke"
sys.path.insert(0, str(SMOKE))
sys.path.insert(0, str(HERE))

import budget_proxy  # noqa: E402
import run_smoke  # noqa: E402

FAMILIES = {
    "i": "families.i_family",
    "e": "families.e_family",
    "t": "families.t_family",
    "tm": "families.t_mech_family",
    "lb": "families.lb_family",
}
TREATMENTS = (
    "r1-s1-m1",
    "r1-s1-m0",
    "r1-s0-m1",
    "r1-s0-m0",
    "r0-s1-m1",
    "r0-s1-m0",
    "r0-s0-m1",
    "r0-s0-m0",
)


def run_id_for(model: str, treatment: str, task_id: str) -> str:
    return f"{model}__{treatment}__{task_id}"


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def budget_policy(config: dict[str, Any]) -> budget_proxy.BudgetPolicy:
    return budget_proxy.BudgetPolicy.from_manifest(
        {
            "input_usd_per_million": config["input_usd_per_million"],
            "output_usd_per_million": config["output_usd_per_million"],
            "context_tokens": config["context_tokens"],
            "response_tokens": config["response_tokens"],
            "max_provider_requests": config["max_total_requests"],
            "task_request_quota": {},
            "max_transport_retries": config["max_transport_retries"],
            "usd_to_cny": config["usd_to_cny"],
            "hard_budget_cny": config["hard_budget_cny"],
        }
    )


def families_for(config: dict[str, Any]) -> dict[str, Any]:
    import importlib

    loaded = {}
    for key in config["families"]:
        module = importlib.import_module(FAMILIES[key])
        loaded[key] = module
    return loaded


def planned_runs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The full matrix: every enabled model x treatment x family instance."""
    runs = []
    loaded = families_for(config)
    treatments = config.get("treatments") or TREATMENTS
    for model_key in config["models"]:
        for treatment in treatments:
            for family_key, instances in config["families"].items():
                if not instances:
                    continue
                module = loaded[family_key]
                for instance in instances:
                    task_id = f"{family_key}-{instance:02d}"
                    runs.append(
                        {
                            "run_id": run_id_for(model_key, treatment, task_id),
                            "model": model_key,
                            "treatment": treatment,
                            "family": family_key,
                            "task_id": task_id,
                            "instance": instance,
                            "module": module,
                        }
                    )
    return runs


def write_models_config(
    agent_dir: Path, config: dict[str, Any], model_id: str, base_url: str
) -> None:
    models = {
        "providers": {
            config["provider"]: {
                "api": "openai-completions",
                "baseUrl": base_url,
                "models": [
                    {
                        "id": model_id,
                        "name": model_id,
                        "reasoning": False,
                        "contextWindow": int(config["context_tokens"]),
                        "maxTokens": int(config["response_tokens"]),
                    }
                ],
            }
        }
    }
    settings = {
        "defaultProvider": config["provider"],
        "defaultModel": model_id,
        "prewarmPythonKernel": False,
        "retry": {"enabled": False},
    }
    (agent_dir / "models.json").write_text(
        json.dumps(models, separators=(",", ":")), encoding="utf-8"
    )
    (agent_dir / "settings.json").write_text(
        json.dumps(settings, separators=(",", ":")), encoding="utf-8"
    )


def build_command(binary: Path, config: dict[str, Any], model_id: str, prompt: str) -> list[str]:
    return [
        str(binary),
        "--mode",
        "json",
        "--provider",
        config["provider"],
        "--model",
        model_id,
        "-p",
        prompt,
    ]


def load_done(records_path: Path) -> set[str]:
    if not records_path.is_file():
        return set()
    done = set()
    for line in records_path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("run_id") and record.get("status") == "completed":
            done.add(record["run_id"])
    return done


def append_record(records_path: Path, record: dict[str, Any]) -> None:
    records_path.parent.mkdir(parents=True, exist_ok=True)
    with records_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n")


def summarize(records: list[dict[str, Any]], config: dict[str, Any]) -> dict[str, Any]:
    cells: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("status") == "completed":
            cells.setdefault(record["treatment"], []).append(record)
    treatments = {}
    for treatment in TREATMENTS:
        rows = cells.get(treatment, [])
        if not rows:
            continue
        treatments[treatment] = {
            "runs": len(rows),
            "success_rate": round(sum(1 for row in rows if row.get("success")) / len(rows), 4),
            "mean_score": round(sum(float(row.get("score", 0)) for row in rows) / len(rows), 4),
        }
    usage = {
        "prompt_tokens": sum(int(row.get("usage", {}).get("prompt_tokens", 0)) for row in records),
        "completion_tokens": sum(
            int(row.get("usage", {}).get("completion_tokens", 0)) for row in records
        ),
        "actual_cost_cny": round(
            sum(float(row.get("usage", {}).get("actual_cost_cny", 0)) for row in records), 6
        ),
    }
    return {
        "mode": config["mode"],
        "config": {key: config[key] for key in ("models", "families", "hard_budget_cny")},
        "counts": {
            "completed": sum(1 for row in records if row.get("status") == "completed"),
            "failed": sum(1 for row in records if row.get("status") not in (None, "completed")),
            "total": len(records),
        },
        "treatments": treatments,
        "usage": usage,
    }


def _episodes(case: Any) -> list[Any]:
    """A case is one episode, or an ordered multi-episode series."""
    episodes = getattr(case, "episodes", None)
    if episodes is not None:
        return list(episodes)
    return [
        types.SimpleNamespace(
            name="main",
            workdir=case.workdir,
            prompt=case.prompt,
            reference=getattr(case, "reference", ""),
            scored=True,
        )
    ]


def _verify_episode(module: Any, case: Any, index: int, text: str) -> dict[str, Any]:
    verify_episode = getattr(module, "verify_episode", None)
    if verify_episode is not None:
        return verify_episode(case, index, text)
    return module.verify(case, text)


def run_matrix(
    config: dict[str, Any],
    binary: Path,
    results_dir: Path,
    mode: str,
    limit: int = 0,
) -> list[dict[str, Any]]:
    results_dir.mkdir(parents=True, exist_ok=True)
    records_path = results_dir / "records.jsonl"
    done = load_done(records_path)
    runs = planned_runs(config)
    api_key = None
    proxy = None
    ledger = None
    if mode == "live":
        api_key = run_smoke.read_keychain_key()
        proxy = budget_proxy.start_proxy(budget_policy(config))
        ledger = proxy.ledger
    records: list[dict[str, Any]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="prime-agent-ablation-") as directory:
            root = Path(directory)
            for run in runs[:limit] if limit else runs:
                if run["run_id"] in done:
                    continue
                module = run["module"]
                case = module.materialize(
                    run["instance"], root / "tasks" / run["task_id"]
                )
                episodes = _episodes(case)
                record: dict[str, Any] = {
                    "run_id": run["run_id"],
                    "model": run["model"],
                    "treatment": run["treatment"],
                    "family": run["family"],
                    "task_id": case.task_id,
                }
                started = time.monotonic()
                if mode == "dry-run":
                    dry_verify = getattr(module, "dry_run_verify", None)
                    verification = (
                        dry_verify(case)
                        if dry_verify is not None
                        else module.verify(case, getattr(case, "reference", ""))
                    )
                    record.update(
                        {
                            "status": "completed",
                            "success": verification["success"],
                            "score": verification["score"],
                            "latency_seconds": round(time.monotonic() - started, 3),
                        }
                    )
                    records.append(record)
                    append_record(records_path, record)
                    continue
                if ledger is not None:
                    summary = ledger.summary()
                    next_worst = budget_policy(config).worst_case_request_cny()
                    if summary["actual_cost_cny"] + next_worst > config["hard_budget_cny"]:
                        record["status"] = "budget_stop"
                        records.append(record)
                        append_record(records_path, record)
                        break
                    ledger.begin_task(run["run_id"], quota=config["max_requests_per_run"])
                model_id = config["models"][run["model"]]
                task_root = root / "runs" / run["run_id"]
                # M-on shares one agent directory across the series; M-off gives
                # every episode a fresh one so nothing durable can carry over.
                shared_agent = run["treatment"].endswith("m1")
                before_cell = ledger.summary() if ledger is not None else None
                episode_records: list[dict[str, Any]] = []
                agent_dirs: list[Path] = []
                status = "completed"
                for index, episode in enumerate(episodes):
                    agent_dir = task_root / (
                        "agent" if shared_agent else f"agent-ep{index}"
                    )
                    agent_dir.mkdir(parents=True, exist_ok=True)
                    agent_dirs.append(agent_dir)
                    # M-off must have no durable channel at all, so even HOME,
                    # TMPDIR and the session dir are fresh per episode; M-on
                    # shares the episode root with the series.
                    episode_root = task_root if shared_agent else task_root / f"ep{index}"
                    env = run_smoke.isolated_env(episode_root, api_key)
                    env["PRIME_AGENT_CODING_AGENT_DIR"] = str(agent_dir)
                    env["PRIME_AGENT_HARNESS_TREATMENT"] = run["treatment"]
                    env["PRIME_AGENT_SESSION_DIR"] = str(task_root / f"sessions-ep{index}")
                    write_models_config(
                        agent_dir,
                        config,
                        model_id,
                        proxy.base_url if proxy is not None else "",
                    )
                    before = ledger.summary() if ledger is not None else None
                    episode_started = time.monotonic()
                    try:
                        completed = subprocess.run(
                            build_command(binary, config, model_id, episode.prompt),
                            cwd=episode.workdir,
                            env=env,
                            capture_output=True,
                            text=True,
                            timeout=config["task_timeout_seconds"],
                            check=False,
                        )
                    except subprocess.TimeoutExpired:
                        episode_records.append(
                            {
                                "name": episode.name,
                                "status": "timeout",
                                "scored": episode.scored,
                                "score": 0.0,
                                "success": False,
                                "latency_seconds": round(
                                    time.monotonic() - episode_started, 3
                                ),
                            }
                        )
                        status = "timeout"
                        break
                    text = run_smoke._assistant_text(completed.stdout)
                    verification = _verify_episode(module, case, index, text)
                    episode_row: dict[str, Any] = {
                        "name": episode.name,
                        "status": "completed"
                        if completed.returncode == 0
                        else "process_error",
                        "scored": episode.scored,
                        "score": verification["score"],
                        "success": verification["success"],
                        "latency_seconds": round(time.monotonic() - episode_started, 3),
                    }
                    details = {
                        key: value
                        for key, value in verification.items()
                        if key not in ("success", "score")
                    }
                    if details:
                        episode_row["details"] = details
                    if before is not None and ledger is not None:
                        after = ledger.summary()
                        episode_row["usage"] = _usage_delta(before, after)
                    episode_records.append(episode_row)
                    if completed.returncode != 0:
                        status = "process_error"
                        break
                scored = [row for row in episode_records if row["scored"]]
                record["episodes"] = episode_records
                record["harness_state_bytes"] = sum(
                    _file_size(directory / "harness" / "harness_state.json")
                    for directory in agent_dirs
                )
                record["status"] = status
                record["success"] = bool(scored) and all(
                    row["success"] for row in scored
                )
                record["score"] = (
                    round(sum(row["score"] for row in scored) / len(scored), 6)
                    if scored
                    else 0.0
                )
                record["latency_seconds"] = round(time.monotonic() - started, 3)
                if before_cell is not None and ledger is not None:
                    after_cell = ledger.summary()
                    record["usage"] = _usage_delta(before_cell, after_cell)
                    if after_cell["error_categories"] != before_cell["error_categories"]:
                        record["error_categories"] = after_cell["error_categories"]
                        record["status"] = "infra_error"
                records.append(record)
                append_record(records_path, record)
    finally:
        if proxy is not None:
            proxy.close()
    summary = summarize(records, config)
    (results_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return records


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _usage_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "request_count": after["request_count"] - before["request_count"],
        "prompt_tokens": after["prompt_tokens"] - before["prompt_tokens"],
        "completion_tokens": after["completion_tokens"] - before["completion_tokens"],
        "actual_cost_cny": round(
            after["actual_cost_cny"] - before["actual_cost_cny"], 6
        ),
    }



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--binary", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--mode", choices=("dry-run", "live"), default="dry-run")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args(argv)
    config = load_config(args.config)
    config["mode"] = args.mode
    run_matrix(config, args.binary.resolve(), args.results, args.mode, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
