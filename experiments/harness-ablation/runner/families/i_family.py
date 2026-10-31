"""I-family pilot tasks: multi-file retrieval, joins, and contradiction rules.

Each instance is three tables (orders, customers, refunds) whose answers need a
join, a date window, and a stated rule for contradictory evidence: the refunds
table is authoritative over the order's own status. Four scalar/count answers
plus one date are checked field by field.
"""

from __future__ import annotations

import csv
import json
import random
import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

REGIONS = ("AMER", "APAC", "EMEA")
TIERS = ("gold", "silver", "bronze")
TIER_WEIGHTS = (2, 4, 5)
STATUSES = ("approved", "failed", "refunded")
STATUS_WEIGHTS = (14, 3, 3)
WINDOW_START = date(2025, 2, 1)
WINDOW_END = date(2025, 3, 31)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
FIELDS = (
    "revenue_cents",
    "gold_orders",
    "busiest_weekday",
    "top_refund_customer",
    "contradictory_orders",
    "earliest_gold_order",
)


@dataclass(frozen=True)
class Case:
    task_id: str
    workdir: Path
    prompt: str
    reference: str
    expected: dict[str, Any]


def _iso_day(start: date, end: date, rng: random.Random) -> str:
    return (start + timedelta(days=rng.randint(0, (end - start).days))).isoformat()


def _tables(seed: int) -> tuple[list[dict], list[dict], list[dict]]:
    rng = random.Random(seed)
    customers = [
        {
            "customer_id": f"c{index:02d}",
            "region": rng.choice(REGIONS),
            "tier": rng.choices(TIERS, weights=TIER_WEIGHTS)[0],
            "created_date": _iso_day(date(2025, 1, 1), date(2025, 1, 31), rng),
        }
        for index in range(1, 25)
    ]
    by_id = {customer["customer_id"]: customer for customer in customers}
    orders = []
    for index in range(320):
        customer = rng.choice(customers)["customer_id"]
        orders.append(
            {
                "order_id": f"o{index:04d}",
                "customer_id": customer,
                "date": _iso_day(date(2025, 1, 5), date(2025, 4, 20), rng),
                "amount_cents": rng.randrange(200, 90_000),
                "status": rng.choices(STATUSES, weights=STATUS_WEIGHTS)[0],
            }
        )
    refunds = []
    for order in orders:
        if order["status"] == "refunded" and rng.random() < 0.7:
            partial = rng.random() < 0.25
            refunds.append(
                {
                    "order_id": order["order_id"],
                    "refund_date": order["date"],
                    "amount_cents": order["amount_cents"] // 2 if partial else order["amount_cents"],
                }
            )
        elif order["status"] == "approved" and rng.random() < 0.05:
            refunds.append(
                {
                    "order_id": order["order_id"],
                    "refund_date": order["date"],
                    "amount_cents": order["amount_cents"],
                }
            )
    return orders, list(by_id.values()), refunds


def _expected(orders: list[dict], customers: list[dict], refunds: list[dict]) -> dict[str, Any]:
    tier = {customer["customer_id"]: customer["tier"] for customer in customers}
    refunded = {}
    for refund in refunds:
        refunded[refund["order_id"]] = refunded.get(refund["order_id"], 0) + refund["amount_cents"]

    def in_window(order: dict) -> bool:
        day = date.fromisoformat(order["date"])
        return WINDOW_START <= day <= WINDOW_END

    window = [order for order in orders if in_window(order)]
    # The rule is authoritative: a refund row OR status == "refunded" means
    # refunded, even when the two sources disagree.
    refunded_ids = set(refunded) | {
        order["order_id"] for order in orders if order["status"] == "refunded"
    }
    active = [order for order in window if order["order_id"] not in refunded_ids]
    revenue = sum(order["amount_cents"] for order in active)
    gold = [
        order
        for order in window
        if tier[order["customer_id"]] == "gold"
    ]
    weekday_counts: dict[str, int] = {}
    for order in window:
        name = WEEKDAYS[date.fromisoformat(order["date"]).weekday()]
        weekday_counts[name] = weekday_counts.get(name, 0) + 1
    busiest = sorted(weekday_counts.items(), key=lambda item: (-item[1], item[0]))[0][0]

    refund_by_customer: dict[str, int] = {}
    by_order = {order["order_id"]: order for order in orders}
    for refund in refunds:
        order = by_order[refund["order_id"]]
        refund_by_customer[order["customer_id"]] = (
            refund_by_customer.get(order["customer_id"], 0) + refund["amount_cents"]
        )
    top_refund = sorted(refund_by_customer.items(), key=lambda item: (-item[1], item[0]))[0][0]

    contradictions = sum(
        1
        for order in window
        if (order["status"] == "refunded") != (order["order_id"] in refunded)
    )
    earliest_gold = min((order["date"] for order in gold), default="")
    return {
        "revenue_cents": revenue,
        "gold_orders": len(gold),
        "busiest_weekday": busiest,
        "top_refund_customer": top_refund,
        "contradictory_orders": contradictions,
        "earliest_gold_order": earliest_gold,
    }


def _write_csv(path: Path, rows: list[dict], fields: tuple[str, ...]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def materialize(instance: int, root: Path) -> Case:
    seed = 7000 + instance
    orders, customers, refunds = _tables(seed)
    expected = _expected(orders, customers, refunds)
    workdir = root / f"i-{instance:02d}"
    workdir.mkdir(parents=True, exist_ok=True)
    _write_csv(
        workdir / "orders.csv",
        orders,
        ("order_id", "customer_id", "date", "amount_cents", "status"),
    )
    _write_csv(
        workdir / "customers.csv",
        customers,
        ("customer_id", "region", "tier", "created_date"),
    )
    _write_csv(
        workdir / "refunds.csv",
        refunds,
        ("order_id", "refund_date", "amount_cents"),
    )
    prompt = (
        "Read orders.csv, customers.csv, and refunds.csv in the current directory.\n"
        "Rules:\n"
        "- The window is 2025-02-01 through 2025-03-31 inclusive (order date).\n"
        "- refunds.csv is authoritative: an order counts as refunded when it has a "
        "refund row, even if orders.status says otherwise; orders.status == "
        '"refunded" without a refund row also counts as refunded.\n'
        "- revenue_cents: sum of amount_cents over window orders that are NOT refunded.\n"
        "- gold_orders: count of window orders whose customer tier is gold "
        "(refunded or not).\n"
        "- busiest_weekday: weekday name (Monday..Sunday) with the most window orders; "
        "alphabetical tie-break.\n"
        "- top_refund_customer: customer_id with the largest total refund amount "
        "across all refund rows (alphabetical tie-break).\n"
        "- contradictory_orders: count of window orders where 'status == refunded' "
        "disagrees with 'has a refund row' (exclusive or).\n"
        "- earliest_gold_order: earliest date (YYYY-MM-DD) among window gold orders.\n"
        "Answer with ONE JSON object and nothing else, with exactly these keys. "
        "Use integers for counts and cents."
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
    """Score the six fields; success requires every field to match."""
    parsed = parse_output(output)
    details: dict[str, Any] = {"parsed": parsed is not None}
    if parsed is None:
        return {"success": False, "score": 0.0, **details}
    correct = 0
    for field in FIELDS:
        actual = parsed.get(field)
        wanted = case.expected[field]
        if isinstance(wanted, int) and not isinstance(wanted, bool):
            matched = (
                isinstance(actual, int) and not isinstance(actual, bool) and actual == wanted
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
