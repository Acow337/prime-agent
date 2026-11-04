"""Equivalence-style summary for the paper's null results.

For each headline comparison, report the observed difference, a bootstrap 95%
confidence interval, the minimum detectable effect (the interval's half-width),
and a two one-sided test (TOST) against a pre-stated equivalence margin. This is
what "we detect no difference" should mean; a non-significant p-value alone is
not evidence of absence.
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from statistics import mean

BOOTSTRAP = 4000
SEED = 7


def load(results: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (results / "records.jsonl").read_text().splitlines()
        if line.strip() and json.loads(line)["status"] == "completed"
    ]


def means_diff(a: list[float], b: list[float]) -> float:
    return mean(a) - mean(b)


def bootstrap_ci(a: list[float], b: list[float], rng: random.Random) -> tuple[float, float]:
    diffs = []
    for _ in range(BOOTSTRAP):
        sample_a = [rng.choice(a) for _ in a]
        sample_b = [rng.choice(b) for _ in b]
        diffs.append(mean(sample_a) - mean(sample_b))
    diffs.sort()
    low = diffs[int(0.025 * len(diffs))]
    high = diffs[int(0.975 * len(diffs))]
    return low, high


def tost(low: float, high: float, margin: float) -> tuple[bool, bool]:
    """Return (equivalent at margin, interval inside margin)."""
    return (low > -margin and high < margin), (low > -margin and high < margin)


def compare(name: str, a: list[float], b: list[float], margin: float, rng: random.Random) -> dict:
    diff = means_diff(a, b)
    low, high = bootstrap_ci(a, b, rng)
    mde = max(abs(low - diff), abs(high - diff))
    equivalent = low > -margin and high < margin
    return {
        "comparison": name,
        "n_a": len(a),
        "n_b": len(b),
        "mean_a": mean(a),
        "mean_b": mean(b),
        "diff": diff,
        "ci95": [low, high],
        "mde": mde,
        "margin": margin,
        "equivalent": equivalent,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--main", required=True, type=Path)
    parser.add_argument("--longbench", required=True, type=Path)
    parser.add_argument("--longmemeval", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    rng = random.Random(SEED)

    rows = load(args.main)
    t = [row for row in rows if row["family"] == "t"]
    t_m1 = [row["score"] for row in t if row["treatment"].endswith("m1")]
    t_m0 = [row["score"] for row in t if row["treatment"].endswith("m0")]
    rows_lb = load(args.longbench)
    lb_full = [row["score"] for row in rows_lb if row["treatment"] == "r1-s1-m1"]
    lb_off = [row["score"] for row in rows_lb if row["treatment"] == "r0-s0-m0"]
    rows_lme = load(args.longmemeval)
    m_m1 = [row["score"] for row in rows_lme if row["treatment"].endswith("m1")]
    m_m0 = [row["score"] for row in rows_lme if row["treatment"].endswith("m0")]

    results = [
        compare("T: M-on vs M-off (score)", t_m1, t_m0, 0.02, rng),
        compare("LongBench: full vs all-off (score)", lb_full, lb_off, 0.10, rng),
        compare("LongMemEval: M-on vs M-off (score)", m_m1, m_m0, 0.10, rng),
    ]

    lines = ["# Equivalence summary", ""]
    lines.append(
        "Bootstrap 95% intervals (4000 resamples). The minimum detectable effect (MDE) "
        "is the interval half-width: effects smaller than it are not ruled out, they are "
        "simply not resolved by this design."
    )
    lines.append("")
    lines.append("| comparison | n (a/b) | mean a | mean b | diff | 95% CI | MDE | margin | equivalent |")
    lines.append("| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |")
    for result in results:
        low, high = result["ci95"]
        lines.append(
            f"| {result['comparison']} | {result['n_a']}/{result['n_b']} | "
            f"{result['mean_a']:.3f} | {result['mean_b']:.3f} | {result['diff']:+.3f} | "
            f"[{low:+.3f}, {high:+.3f}] | {result['mde']:.3f} | {result['margin']:.2f} | "
            f"{'yes' if result['equivalent'] else 'no'} |"
        )
    lines.append("")
    lines.append(
        "SWE-bench is reported as resolved counts by the official harness "
        "(10/12 full, 10/12 all-off); with twelve paired instances the exact "
        "interval around a zero difference spans roughly ±0.27."
    )
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
