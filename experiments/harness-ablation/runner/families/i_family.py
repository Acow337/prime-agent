"""I-family pilot tasks: long-table retrieval, aggregation, and joins.

Every instance is a deterministic events table plus four machine-checkable
answers computed from it. The prompt asks for one JSON object; the verifier
compares each field, so the score is the fraction of correct fields and
success requires all four.
"""

from __future__ import annotations

import csv
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REGIONS = ("AMER", "APAC", "EMEA")
CATEGORIES = ("billing", "search", "sync")
STATUSES = ("approved", "failed", "refunded")
FIELDS = (
    "total_refunded_eur",
    "busiest_region_approved",
    "top_category_failed",
    "earliest_failed_date",
)


@dataclass(frozen=True)
class Case:
    task_id: str
    workdir: Path
    prompt: str
    reference: str
    expected: dict[str, Any]


def _rows(seed: int, count: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    rows = []
    for index in range(count):
        day = rng.randint(1, 28)
        month = rng.choice((1, 2, 3))
        rows.append(
            {
                "event_id": f"ev-{seed:02d}-{index:04d}",
                "date": f"2025-{month:02d}-{day:02d}",
                "region": rng.choice(REGIONS),
                "category": rng.choice(CATEGORIES),
                "status": rng.choices(STATUSES, weights=(6, 3, 2))[0],
                "amount_eur": round(rng.uniform(5, 500), 2),
            }
        )
    return rows


def _expected(rows: list[dict[str, Any]]) -> dict[str, Any]:
    refunded = round(
        sum(row["amount_eur"] for row in rows if row["status"] == "refunded"), 2
    )
    approved = [row for row in rows if row["status"] == "approved"]
    failed = [row for row in rows if row["status"] == "failed"]

    def top(values: list[str]) -> str:
        counts: dict[str, int] = {}
        for value in values:
            counts[value] = counts.get(value, 0) + 1
        return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    earliest_failed = min((row["date"] for row in failed), default="")
    return {
        "total_refunded_eur": refunded,
        "busiest_region_approved": top([row["region"] for row in approved]),
        "top_category_failed": top([row["category"] for row in failed]),
        "earliest_failed_date": earliest_failed,
    }


def materialize(instance: int, root: Path) -> Case:
    """Create one deterministic events-table instance and its answers."""
    seed = 1000 + instance
    rows = _rows(seed, 240)
    expected = _expected(rows)
    workdir = root / f"i-{instance:02d}"
    workdir.mkdir(parents=True, exist_ok=True)
    with (workdir / "events.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("event_id", "date", "region", "category", "status", "amount_eur"),
        )
        writer.writeheader()
        writer.writerows(rows)
    prompt = (
        "Read events.csv in the current directory. It has columns event_id, date, "
        "region, category, status, amount_eur.\n"
        "Answer with ONE JSON object and nothing else, with exactly these keys:\n"
        '- "total_refunded_eur": sum of amount_eur over status == "refunded", rounded to 2 decimals;\n'
        '- "busiest_region_approved": region with the most status == "approved" events '
        "(alphabetical tie-break);\n"
        '- "top_category_failed": category with the most status == "failed" events '
        "(alphabetical tie-break);\n"
        '- "earliest_failed_date": earliest date (YYYY-MM-DD) among status == "failed" events.\n'
        "Do not run tools unless you need them; a correct JSON answer is enough."
    )
    return Case(
        task_id=f"i-{instance:02d}",
        workdir=workdir,
        prompt=prompt,
        reference=json.dumps(expected),
        expected=expected,
    )


def parse_output(text: str) -> dict[str, Any] | None:
    """Extract the last JSON object from fenced or plain model text."""
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates += re.findall(r"\{[^{}]*\}", text, re.S)
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def verify(case: Case, output: str) -> dict[str, Any]:
    """Score the four fields; success requires every field to match."""
    parsed = parse_output(output)
    details: dict[str, Any] = {"parsed": parsed is not None}
    if parsed is None:
        return {"success": False, "score": 0.0, **details}
    correct = 0
    for field in FIELDS:
        actual = parsed.get(field)
        wanted = case.expected[field]
        if isinstance(wanted, float):
            matched = (
                isinstance(actual, (int, float))
                and not isinstance(actual, bool)
                and abs(float(actual) - wanted) <= 0.011
            )
        else:
            matched = actual == wanted
        if matched:
            correct += 1
        else:
            details[field] = {"expected": wanted, "actual": actual}
    return {
        "success": correct == len(FIELDS),
        "score": correct / len(FIELDS),
        **details,
    }
