"""Analyze a matrix run: accuracy, cost, transfer split, and effect sizes.

Reads the runner's records.jsonl (duplicate run ids collapse to their completed
record; infrastructure retries are ignored) and prints a markdown report with:

- per family x treatment success rate, mean score, requests, tokens, cost,
- the transfer effect: M-on versus M-off inside the T family, with a Fisher
  exact test and a bootstrap confidence interval on the mean-score difference,
- the same split by T template (positive versus negative transfer).
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def load_records(path: Path) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("status") != "completed":
            continue
        by_id[record["run_id"]] = record
    return list(by_id.values())


def fisher_exact(a: int, n1: int, b: int, n2: int) -> float:
    """Two-sided Fisher exact p for a 2x2 table."""
    total = n1 + n2
    successes = a + b

    def prob(x: int) -> float:
        return math.comb(n1, x) * math.comb(n2, successes - x) / math.comb(total, successes)

    observed = abs(a / n1 - b / n2) if n1 and n2 else 0.0
    p = 0.0
    for x in range(max(0, successes - n2), min(successes, n1) + 1):
        if abs(x / n1 - (successes - x) / n2) >= observed - 1e-12:
            p += prob(x)
    return min(1.0, p)


def bootstrap_diff(
    left: list[float], right: list[float], iterations: int = 5000, seed: int = 7
) -> tuple[float, float]:
    rng = random.Random(seed)
    diffs = []
    for _ in range(iterations):
        sample_left = [rng.choice(left) for _ in left]
        sample_right = [rng.choice(right) for _ in right]
        diffs.append(statistics.mean(sample_left) - statistics.mean(sample_right))
    diffs.sort()
    lo = diffs[int(0.025 * len(diffs))]
    hi = diffs[int(0.975 * len(diffs)) - 1]
    return lo, hi


def group(records: list[dict[str, Any]], key) -> dict[Any, list[dict[str, Any]]]:
    grouped: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[key(record)].append(record)
    return grouped


def stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    usage = [row.get("usage", {}) for row in rows]
    return {
        "n": len(rows),
        "success": sum(1 for row in rows if row.get("success")),
        "mean_score": round(statistics.mean(row["score"] for row in rows), 4),
        "mean_requests": round(statistics.mean(u.get("request_count", 0) for u in usage), 2),
        "prompt_tokens": sum(u.get("prompt_tokens", 0) for u in usage),
        "cost_cny": round(sum(u.get("actual_cost_cny", 0) for u in usage), 4),
    }


def template_of(task_id: str) -> tuple[str, bool]:
    """Map t-NN back to the template name and its transfer polarity."""
    import sys

    sys.path.insert(0, str(HERE))
    from families import t_family

    instance = int(task_id.split("-")[1])
    template = t_family.TEMPLATES[(instance - 1) % len(t_family.TEMPLATES)]
    return template.name, template.negative


def report(records: list[dict[str, Any]]) -> str:
    lines = ["# Matrix run analysis", ""]
    lines += [f"- completed cells: {len(records)}", ""]
    lines += ["## Family x treatment", ""]
    lines += [
        "| family | treatment | n | success | mean score | requests | prompt tok | cost CNY |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for (family, treatment), rows in sorted(
        group(records, lambda r: (r["family"], r["treatment"])).items()
    ):
        s = stats(rows)
        lines.append(
            f"| {family} | {treatment} | {s['n']} | {s['success']}/{s['n']} | "
            f"{s['mean_score']} | {s['mean_requests']} | {s['prompt_tokens']} | {s['cost_cny']} |"
        )

    lines += ["", "## Transfer effect inside T", ""]
    t_rows = [r for r in records if r["family"] == "t"]
    if t_rows:
        on = [r for r in t_rows if r["treatment"].endswith("m1")]
        off = [r for r in t_rows if r["treatment"].endswith("m0")]
        s_on, s_off = stats(on), stats(off)
        p_value = fisher_exact(s_on["success"], s_on["n"], s_off["success"], s_off["n"])
        lo, hi = bootstrap_diff(
            [r["score"] for r in on], [r["score"] for r in off]
        ) if on and off else (0.0, 0.0)
        lines.append(
            f"- M-on: {s_on['success']}/{s_on['n']} success, mean score {s_on['mean_score']}"
        )
        lines.append(
            f"- M-off: {s_off['success']}/{s_off['n']} success, mean score {s_off['mean_score']}"
        )
        lines.append(f"- Fisher exact p = {p_value:.5f}")
        lines.append(
            f"- bootstrap 95% CI for mean-score difference: [{lo:.3f}, {hi:.3f}]"
        )
        lines += ["", "### By template", ""]
        lines += [
            "| template | polarity | M-on | M-off | M-on score | M-off score |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
        by_template: dict[tuple[str, bool], dict[str, list[dict[str, Any]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for row in t_rows:
            name, negative = template_of(row["task_id"])
            arm = "on" if row["treatment"].endswith("m1") else "off"
            by_template[(name, negative)][arm].append(row)
        for (name, negative), arms in sorted(by_template.items()):
            on_rows, off_rows = arms.get("on", []), arms.get("off", [])
            lines.append(
                f"| {name} | {'negative' if negative else 'positive'} | "
                f"{sum(1 for r in on_rows if r['success'])}/{len(on_rows)} | "
                f"{sum(1 for r in off_rows if r['success'])}/{len(off_rows)} | "
                f"{round(statistics.mean(r['score'] for r in on_rows), 2) if on_rows else 0} | "
                f"{round(statistics.mean(r['score'] for r in off_rows), 2) if off_rows else 0} |"
            )
    else:
        lines.append("- no T-family cells yet")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    records = load_records(args.results / "records.jsonl")
    text = report(records)
    if args.out is not None:
        args.out.write_text(text, encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
