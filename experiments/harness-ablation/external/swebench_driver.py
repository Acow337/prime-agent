"""SWE-bench Verified driver: prepare agent patches, then evaluate with the
official harness.

prepare:
  For each selected instance and treatment configuration, clone the repository at
  the base commit into a fresh directory, run the headless agent on the problem
  statement under that treatment, and record `git diff` as the prediction.

evaluate:
  Call the official `swebench.harness.run_evaluation` (Docker required) on the
  predictions file. SWE-bench Verified images are x86_64; this host is arm64, so
  Docker must emulate amd64 (colima with vz + Rosetta does here).

The dataset is read through Hugging Face with the repository-local cache
(`HF_HOME=.toolchain/hf`), so no home-directory writes are needed.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
RUNNER = HERE.parent / "runner"
sys.path.insert(0, str(RUNNER))
sys.path.insert(0, str(HERE.parent / "smoke"))

import budget_proxy  # noqa: E402
import runner as matrix  # noqa: E402
import run_smoke  # noqa: E402

DATASET = "SWE-bench/SWE-bench_Verified"
SWEBENCH_PYTHON = HERE.parents[2] / ".toolchain" / "swebench-venv" / "bin" / "python"
CONFIGS = ("r1-s1-m1", "r0-s0-m0")
INSTRUCTIONS = (
    "\n\nFix the issue in this repository. You may read and edit files; do not "
    "modify any test files or add new tests. Make the smallest correct change. "
    "When finished, stop."
)


def load_instances(instance_ids: list[str] | None) -> list[dict[str, Any]]:
    os.environ.setdefault("HF_HOME", str(HERE.parents[2] / ".toolchain" / "hf"))
    from datasets import load_dataset

    rows = load_dataset(DATASET, split="test")
    if instance_ids:
        wanted = set(instance_ids)
        rows = [row for row in rows if row["instance_id"] in wanted]
    return list(rows)


def pick_by_repo(rows: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    """One instance per repository, in dataset order, deterministic."""
    seen: set[str] = set()
    picked = []
    for row in rows:
        if row["repo"] in seen:
            continue
        seen.add(row["repo"])
        picked.append(row)
        if len(picked) == count:
            break
    return picked


def clone_at(workdir: Path, repo: str, commit: str) -> None:
    workdir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(workdir)], check=True)
    subprocess.run(
        ["git", "-C", str(workdir), "remote", "add", "origin", f"https://github.com/{repo}.git"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(workdir), "fetch", "-q", "--depth", "1", "origin", commit],
        check=True,
        timeout=600,
    )
    subprocess.run(
        ["git", "-C", str(workdir), "checkout", "-q", "FETCH_HEAD"],
        check=True,
    )


def run_agent(
    binary: Path,
    workdir: Path,
    state_root: Path,
    prompt: str,
    treatment: str,
    config: dict[str, Any],
    api_key: str,
    proxy_base: str,
    timeout: int,
) -> int:
    # Agent state lives outside the repository, so the produced patch contains
    # only the repository's own changes.
    env = run_smoke.isolated_env(state_root, api_key)
    # The repository may contain a module with the same name as a kernel
    # dependency (e.g. requests/ in the requests repo); keep the CWD out of
    # sys.path so the kernel's package probe uses the venv, not the checkout.
    env["PYTHONSAFEPATH"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    env["PRIME_AGENT_HARNESS_TREATMENT"] = treatment
    env["PRIME_AGENT_CODING_AGENT_DIR"] = str(state_root / "agent")
    Path(env["PRIME_AGENT_CODING_AGENT_DIR"]).mkdir(parents=True, exist_ok=True)
    matrix.write_models_config(
        Path(env["PRIME_AGENT_CODING_AGENT_DIR"]), config, config["models"]["cheap"], proxy_base
    )
    command = [
        str(binary),
        "--mode",
        "json",
        "--provider",
        config["provider"],
        "--model",
        config["models"]["cheap"],
        "-p",
        prompt,
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124
    return completed.returncode


def worktree_patch(workdir: Path) -> str:
    subprocess.run(["git", "-C", str(workdir), "add", "-A"], check=True)
    diff = subprocess.run(
        ["git", "-C", str(workdir), "diff", "--cached"],
        capture_output=True,
        text=True,
        check=True,
    )
    return diff.stdout


def prepare(args: argparse.Namespace) -> int:
    config = json.loads((HERE / "swebench_config.json").read_text())
    rows = load_instances(args.instances) if args.instances else pick_by_repo(
        load_instances(None), args.count
    )
    results = Path(args.results).resolve()
    results.mkdir(parents=True, exist_ok=True)
    predictions_path = results / "predictions.jsonl"
    api_key = run_smoke.read_keychain_key()
    policy = budget_proxy.BudgetPolicy.from_manifest(
        {
            "input_usd_per_million": config["input_usd_per_million"],
            "output_usd_per_million": config["output_usd_per_million"],
            "context_tokens": config["context_tokens"],
            "response_tokens": config["response_tokens"],
            "max_provider_requests": config["max_requests"],
            "task_request_quota": {},
            "max_transport_retries": config["max_transport_retries"],
            "usd_to_cny": config["usd_to_cny"],
            "hard_budget_cny": config["hard_budget_cny"],
        }
    )
    proxy = budget_proxy.start_proxy(policy)
    try:
        with predictions_path.open("a", encoding="utf-8") as out:
            for row in rows:
                for treatment in CONFIGS:
                    run_id = f"{row['instance_id']}__{treatment}"
                    workdir = results / "repos" / run_id
                    if workdir.exists():
                        continue
                    clone_at(workdir, row["repo"], row["base_commit"])
                    (workdir / ".git" / "info" / "exclude").write_text(
                        "__pycache__/\n*.pyc\n.agent/\n", encoding="utf-8"
                    )
                    code = run_agent(
                        args.binary.resolve(),
                        workdir,
                        results / "agents" / run_id,
                        row["problem_statement"] + INSTRUCTIONS,
                        treatment,
                        config,
                        api_key,
                        proxy.base_url,
                        args.timeout,
                    )
                    patch = worktree_patch(workdir)
                    out.write(
                        json.dumps(
                            {
                                "instance_id": row["instance_id"],
                                "model_name_or_path": f"prime-agent-{treatment}",
                                "model_patch": patch,
                                "meta": {
                                    "treatment": treatment,
                                    "repo": row["repo"],
                                    "exit_code": code,
                                    "patch_bytes": len(patch),
                                },
                            }
                        )
                        + "\n"
                    )
                    out.flush()
                    print(run_id, "exit", code, "patch", len(patch))
    finally:
        proxy.close()
        print("usage", json.dumps(proxy.ledger.summary()))
    print("predictions:", predictions_path)
    return 0


def pull_images(args: argparse.Namespace) -> int:
    rows = load_instances(args.instances) if args.instances else pick_by_repo(
        load_instances(None), args.count
    )
    for row in rows:
        image = row["image"]
        print("pulling", image)
        completed = subprocess.run(
            ["docker", "pull", "--platform", "linux/amd64", image], check=False
        )
        if completed.returncode != 0:
            print("pull failed:", image)
    return 0


def evaluate(args: argparse.Namespace) -> int:
    results = Path(args.results).resolve()
    predictions = results / "predictions.jsonl"
    rows = [json.loads(line) for line in predictions.read_text().splitlines() if line.strip()]
    by_treatment: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_treatment.setdefault(row["model_name_or_path"], []).append(row)
    status = 0
    for model_name, arm_rows in by_treatment.items():
        arm_path = results / f"predictions-{model_name}.jsonl"
        arm_path.write_text(
            "".join(json.dumps(row) + "\n" for row in arm_rows), encoding="utf-8"
        )
        # SWE-bench evaluates one prediction per instance, so each treatment
        # gets its own file and run.
        command = [
            str(SWEBENCH_PYTHON),
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            DATASET,
            "--split",
            "test",
            "--predictions_path",
            str(arm_path),
            "--max_workers",
            str(args.workers),
            "--run_id",
            f"{args.run_id}-{model_name}",
        ]
        print(" ".join(command))
        env = dict(os.environ, DOCKER_DEFAULT_PLATFORM="linux/amd64")
        completed = subprocess.run(command, cwd=HERE, env=env, check=False)
        status = status or completed.returncode
    return status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--instances", nargs="*")
    prep.add_argument("--count", type=int, default=12)
    prep.add_argument("--results", required=True, type=Path)
    prep.add_argument("--binary", required=True, type=Path)
    prep.add_argument("--timeout", type=int, default=1800)
    prep.set_defaults(func=prepare)
    pl = sub.add_parser("pull")
    pl.add_argument("--instances", nargs="*")
    pl.add_argument("--count", type=int, default=12)
    pl.set_defaults(func=pull_images)
    ev = sub.add_parser("evaluate")
    ev.add_argument("--results", required=True, type=Path)
    ev.add_argument("--run-id", default="prime-agent")
    ev.add_argument("--workers", type=int, default=1)
    ev.set_defaults(func=evaluate)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
