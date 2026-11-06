"""Freeze the experiment protocol: code, configuration, and fixture hashes.

The frozen file is the audit anchor for a run: a rerun is comparable only when
these hashes match. It records the runner and family module hashes, the
configuration hash, one digest per materialized task instance, the provider and
the fields pinned on every request, and (when a pilot summary exists) its
aggregate results. For an OpenRouter run it also snapshots what the pinned
endpoint charges and serves, fetched from OpenRouter's public model listing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import runner  # noqa: E402


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def instance_digest(module: Any, instance: int, root: Path) -> str:
    case = module.materialize(instance, root)
    episodes = runner._episodes(case)
    digest = hashlib.sha256()
    for episode in episodes:
        digest.update(episode.name.encode())
        for path in sorted(episode.workdir.rglob("*")):
            if path.is_file():
                digest.update(path.relative_to(episode.workdir).as_posix().encode())
                digest.update(path.read_bytes())
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    config = json.loads(args.config.read_text(encoding="utf-8"))
    families = runner.families_for(config)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=HERE, capture_output=True, text=True, check=False
    ).stdout.strip()
    instances: dict[str, str] = {}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for key, numbers in config["families"].items():
            module = families[key]
            for number in numbers:
                task_id = f"{key}-{number:02d}"
                instances[task_id] = instance_digest(module, number, root / task_id)
    frozen: dict[str, Any] = {
        "frozen_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "commit": commit,
        "config_sha256": sha256_file(args.config),
        "runner_sha256": sha256_file(HERE / "runner.py"),
        "families": {
            key: sha256_file(HERE / "families" / f"{key}_family.py")
            for key in config["families"]
        },
        "instances": dict(sorted(instances.items())),
        "provider": config["provider"],
        "models": config["models"],
        "request_fields": config.get("request_fields", {}),
        "treatments": list(runner.TREATMENTS),
        "limits": {
            "hard_budget_cny": config["hard_budget_cny"],
            "max_total_requests": config["max_total_requests"],
            "max_requests_per_run": config["max_requests_per_run"],
            "task_timeout_seconds": config["task_timeout_seconds"],
        },
    }
    if config["provider"] == "openrouter":
        # The budget gate prices the next request's worst case at the config's
        # rates, so a config priced below the pinned endpoint's listing would
        # let a run overspend its cap.
        tag = config["request_fields"]["provider"]["order"][0]
        prices: dict[str, Any] = {}
        for key, model_id in config["models"].items():
            with urllib.request.urlopen(
                f"https://openrouter.ai/api/v1/models/{model_id}/endpoints", timeout=30
            ) as response:
                endpoints = json.load(response)["data"]["endpoints"]
            matches = [endpoint for endpoint in endpoints if endpoint.get("tag") == tag]
            if len(matches) != 1:
                raise SystemExit(f"{model_id}: OpenRouter lists no single endpoint tagged {tag!r}")
            endpoint = matches[0]
            # A time-of-day override (DeepSeek's weekday peak, for one) can
            # charge more than the base rate; the worst case is the highest.
            rates = [endpoint["pricing"], *endpoint["pricing"].get("overrides", [])]
            for field, listed in (
                ("input_usd_per_million", "prompt"),
                ("output_usd_per_million", "completion"),
            ):
                highest = max(Decimal(rate[listed]) for rate in rates if listed in rate) * 1_000_000
                if Decimal(str(config[field])) < highest:
                    raise SystemExit(
                        f"{model_id}: {field} {config[field]} is below the highest listed "
                        f"{listed} price of {tag} ({highest.normalize()} USD per million)"
                    )
            prices[key] = {
                "model": model_id,
                "endpoint": endpoint["name"],
                "tag": tag,
                "provider_name": endpoint["provider_name"],
                "quantization": endpoint.get("quantization"),
                "context_length": endpoint.get("context_length"),
                "max_completion_tokens": endpoint.get("max_completion_tokens"),
                "pricing": endpoint["pricing"],
                "supported_parameters": endpoint.get("supported_parameters", []),
            }
        frozen["prices"] = prices
    if args.results is not None:
        summary_path = args.results / "summary.json"
        if summary_path.is_file():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            frozen["pilot"] = {
                "results_dir": str(args.results),
                "counts": summary["counts"],
                "usage": summary["usage"],
                "treatments": summary["treatments"],
            }
    args.out.write_text(json.dumps(frozen, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"frozen {len(instances)} instances -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
