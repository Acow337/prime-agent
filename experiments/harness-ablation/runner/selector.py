"""Budget-aware selector: pick one treatment per family under a budget.

Reads the main matrix's counted cells, estimates each family x treatment success
and cost, enumerates all per-family policies (8^3 = 512), and reports the Pareto
frontier together with the always-full, always-off, and oracle policies.

This is an in-sample exercise: the estimates come from the same 30 instances the
policy is scored on. It shows the shape of the trade-off, not a held-out gain.
"""

from __future__ import annotations

import argparse
import itertools
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

import runner

FAMILIES = ("i", "e", "t")
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


def load(results: Path, config: Path) -> dict[tuple[str, str], list[dict]]:
    cells: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for record in runner.load_counted(results, config):
        cells[(record["family"], record["treatment"])].append(record)
    return cells


def summarize(cells: dict[tuple[str, str], list[dict]]) -> dict[str, dict[str, dict[str, float]]]:
    summary: dict[str, dict[str, dict[str, float]]] = {}
    for family in FAMILIES:
        summary[family] = {}
        for treatment in TREATMENTS:
            rows = cells.get((family, treatment), [])
            if not rows:
                continue
            summary[family][treatment] = {
                "score": mean(row["score"] for row in rows),
                "success": mean(1.0 if row["success"] else 0.0 for row in rows),
                "cost": mean(row["usage"]["actual_cost_cny"] for row in rows),
                "requests": mean(row["usage"]["request_count"] for row in rows),
                "n": len(rows),
            }
    return summary


def policy_stats(summary: dict, policy: dict[str, str], tasks_per_family: int = 30) -> tuple[float, float]:
    cost = sum(summary[family][treatment]["cost"] * tasks_per_family for family, treatment in policy.items())
    score = sum(summary[family][treatment]["score"] * tasks_per_family for family, treatment in policy.items())
    total = tasks_per_family * len(policy)
    return cost, score / total


def pareto(points: list[tuple[float, float, dict[str, str]]]) -> list[tuple[float, float, dict[str, str]]]:
    points = sorted(points, key=lambda item: (item[0], -item[1]))
    frontier = []
    best = -1.0
    for cost, score, policy in points:
        if score > best + 1e-9:
            frontier.append((cost, score, policy))
            best = score
    return frontier


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--results", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    summary = summarize(load(args.results, args.config))
    candidates = []
    for policy in itertools.product(TREATMENTS, repeat=len(FAMILIES)):
        mapping = dict(zip(FAMILIES, policy))
        cost, score = policy_stats(summary, mapping)
        candidates.append((cost, score, mapping))
    frontier = pareto(candidates)
    full = {family: "r1-s1-m1" for family in FAMILIES}
    off = {family: "r0-s0-m0" for family in FAMILIES}
    oracle = {
        family: max(summary[family], key=lambda treatment: summary[family][treatment]["score"])
        for family in FAMILIES
    }

    lines = ["# Budget-aware treatment selection", ""]
    lines.append("In-sample estimates from the main matrix's counted cells; 90 tasks total (30 per family).")
    lines.append("")
    lines.append("## Family x treatment")
    lines.append("")
    lines.append("| family | treatment | n | success | score | cost/task (¥) | requests |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: |")
    for family in FAMILIES:
        for treatment in TREATMENTS:
            row = summary[family].get(treatment)
            if row is None:
                continue
            lines.append(
                f"| {family} | `{treatment}` | {row['n']:.0f} | {row['success']:.3f} | "
                f"{row['score']:.4f} | {row['cost']:.3f} | {row['requests']:.1f} |"
            )
    lines.append("")
    lines.append("## Reference policies")
    lines.append("")
    lines.append("| policy | cost (¥/90 tasks) | mean score |")
    lines.append("| --- | ---: | ---: |")
    for name, policy in (("always full", full), ("always off", off), ("oracle per family", oracle)):
        cost, score = policy_stats(summary, policy)
        lines.append(f"| {name} | {cost:.2f} | {score:.4f} |")
    lines.append("")
    lines.append("## Pareto frontier")
    lines.append("")
    lines.append("| cost (¥/90) | mean score | policy (i / e / t) |")
    lines.append("| ---: | ---: | --- |")
    for cost, score, policy in frontier:
        lines.append(
            f"| {cost:.2f} | {score:.4f} | `{policy['i']}` / `{policy['e']}` / `{policy['t']}` |"
        )
    lines.append("")
    cheapest_full_quality = next(
        ((cost, score, policy) for cost, score, policy in frontier if score >= policy_stats(summary, full)[1] - 1e-9),
        None,
    )
    if cheapest_full_quality:
        cost, score, policy = cheapest_full_quality
        lines.append(
            f"Cheapest policy that matches the full harness score: ¥{cost:.2f} vs "
            f"¥{policy_stats(summary, full)[0]:.2f} "
            f"(`{policy['i']}` / `{policy['e']}` / `{policy['t']}`)."
        )
        lines.append("")
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (args.out.parent / "selector.json").write_text(
        json.dumps(
            {
                "summary": summary,
                "frontier": [
                    {"cost": cost, "score": score, "policy": policy} for cost, score, policy in frontier
                ],
                "reference": {
                    "full": policy_stats(summary, full),
                    "off": policy_stats(summary, off),
                    "oracle": policy_stats(summary, oracle),
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.out} with {len(frontier)} frontier points")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
