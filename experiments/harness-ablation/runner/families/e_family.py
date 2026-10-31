"""E-family pilot tasks: fix a small multi-module repository.

Each instance is a tiny orders package with six independent defects:

1. comment lines are parsed as orders,
2. the discount helper truncates instead of rounding half up,
3. the report drops customers whose net total is zero,
4. duplicate order ids are kept instead of keeping the last occurrence,
5. negative amounts and non-positive quantities are accepted,
6. the report sorts by customer only instead of net descending.

The public test exposes only the parser failure; verifier-owned hidden tests
cover all six defects plus an end-to-end total. The score is the hidden-test
pass ratio.
"""

from __future__ import annotations

import dataclasses
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SPEC = """# Orders report

Line format (one order per line):

    order_id|sku|customer|unit_cents|qty

- Blank lines and lines starting with `#` are comments and are skipped.
- `unit_cents` must be >= 0 and `qty` must be >= 1; anything else raises
  `ValueError`, as does a line that does not have exactly five fields.
- When the same `order_id` appears more than once, the LAST occurrence wins.

Rules:

- Line total: `unit_cents * qty`.
- A customer in `config.VIP_CUSTOMERS` gets `config.DISCOUNT_PCT` percent off,
  rounded half up to the nearest cent.
- All customers pay `config.TAX_PCT` percent tax on the discounted amount,
  rounded half up to the nearest cent.

Public helpers (do not rename):

- `pricing.line_total(order) -> int`
- `pricing.discounted_cents(total_cents, customer) -> int` (total after discount)
- `pricing.tax_cents(discounted_cents) -> int`
- `report.build_report(orders) -> list[dict]`

`report.build_report` returns one row per customer (including customers whose
net total is zero), sorted by `net_cents` DESC and then `customer` ASC, with the
keys `customer`, `gross_cents`, `discount_cents`, `tax_cents`, `net_cents`.
"""

PARSER = '''"""Parse order lines."""


def parse_line(line):
    parts = line.split("|")
    if len(parts) != 5:
        raise ValueError(f"malformed order line: {line!r}")
    return {
        "order_id": parts[0],
        "sku": parts[1],
        "customer": parts[2],
        "unit_cents": int(parts[3]),
        "qty": int(parts[4]),
    }


def parse_orders(text):
    orders = []
    for line in text.splitlines():
        if not line.strip():
            continue
        orders.append(parse_line(line))
    return orders
'''

PRICING = '''"""Discount and tax helpers."""

import config


def line_total(order):
    return order["unit_cents"] * order["qty"]


def discounted_cents(total_cents, customer):
    if customer not in config.VIP_CUSTOMERS:
        return total_cents
    return total_cents * (100 - config.DISCOUNT_PCT) // 100


def tax_cents(total_cents):
    return total_cents * config.TAX_PCT // 100
'''

REPORT = '''"""Customer report."""

from collections import OrderedDict

from orders.pricing import discounted_cents, line_total, tax_cents


def build_report(orders):
    rows = OrderedDict()
    for order in orders:
        customer = order["customer"]
        gross = line_total(order)
        discounted = discounted_cents(gross, customer)
        tax = tax_cents(discounted)
        net = discounted + tax
        row = rows.setdefault(
            customer,
            {"customer": customer, "gross_cents": 0, "discount_cents": 0,
             "tax_cents": 0, "net_cents": 0},
        )
        row["gross_cents"] += gross
        row["discount_cents"] += gross - discounted
        row["tax_cents"] += tax
        row["net_cents"] += net
    return [
        row
        for row in sorted(rows.values(), key=lambda item: item["customer"])
        if row["net_cents"] != 0
    ]
'''

PUBLIC_TEST = '''import unittest

from orders.parser import parse_orders


class OrdersPublicTests(unittest.TestCase):
    def test_reports_total_for_one_order(self):
        orders = parse_orders("o1|sku|acme|100|2\\n")
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["unit_cents"], 100)


if __name__ == "__main__":
    unittest.main()
'''

HIDDEN_TESTS = '''import unittest

import config
from orders.parser import parse_line, parse_orders
from orders.pricing import discounted_cents
from orders.report import build_report


def vip():
    return sorted(config.VIP_CUSTOMERS)[0]


def lines():
    return (
        "o1|sku|{vip}|105|1\\n"
        "# comment line\\n"
        "\\n"
        "o2|sku|other|200|2\\n"
    ).format(vip=vip())


class OrdersHiddenTests(unittest.TestCase):
    def test_parser_skips_comments_and_blank_lines(self):
        orders = parse_orders(lines())
        self.assertEqual(len(orders), 2)
        self.assertEqual({order["order_id"] for order in orders}, {"o1", "o2"})

    def test_duplicate_order_ids_keep_the_last_occurrence(self):
        orders = parse_orders(
            "o1|sku|acme|100|1\\no1|sku|acme|250|3\\n"
        )
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["unit_cents"], 250)
        self.assertEqual(orders[0]["qty"], 3)

    def test_negative_amounts_and_bad_quantities_are_rejected(self):
        for line in ("o1|sku|acme|-5|1", "o1|sku|acme|100|0", "o1|sku|acme|100|-2"):
            with self.assertRaises(ValueError):
                parse_line(line)

    def test_discount_rounds_half_up(self):
        amount = next(
            value
            for value in range(100, 400)
            if (value * (100 - config.DISCOUNT_PCT)) % 100 >= 50
        )
        expected = (amount * (100 - config.DISCOUNT_PCT) + 50) // 100
        self.assertEqual(discounted_cents(amount, vip()), expected)

    def test_report_keeps_zero_net_customers(self):
        rows = build_report(
            [
                {"order_id": "z", "sku": "s", "customer": "zero", "unit_cents": 0, "qty": 0},
            ]
        )
        self.assertEqual([row["customer"] for row in rows], ["zero"])

    def test_report_sorts_by_net_descending_then_customer(self):
        rows = build_report(
            [
                {"order_id": "a", "sku": "s", "customer": "alpha", "unit_cents": 100, "qty": 1},
                {"order_id": "b", "sku": "s", "customer": "beta", "unit_cents": 300, "qty": 1},
                {"order_id": "c", "sku": "s", "customer": "gamma", "unit_cents": 200, "qty": 1},
            ]
        )
        self.assertEqual([row["customer"] for row in rows], ["beta", "gamma", "alpha"])

    def test_end_to_end_totals(self):
        rows = build_report(parse_orders(lines()))
        by_customer = {row["customer"]: row for row in rows}
        gross = 105 * 1
        discounted = (gross * (100 - config.DISCOUNT_PCT) + 50) // 100
        tax = (discounted * config.TAX_PCT + 50) // 100
        self.assertEqual(by_customer[vip()]["net_cents"], discounted + tax)
        other_gross = 200 * 2
        other_tax = (other_gross * config.TAX_PCT + 50) // 100
        self.assertEqual(by_customer["other"]["net_cents"], other_gross + other_tax)
'''

FIXES = (
    (
        "orders/parser.py",
        '    return {\n        "order_id": parts[0],\n        "sku": parts[1],\n        "customer": parts[2],\n        "unit_cents": int(parts[3]),\n        "qty": int(parts[4]),\n    }\n',
        '    unit_cents = int(parts[3])\n    qty = int(parts[4])\n    if unit_cents < 0 or qty < 1:\n        raise ValueError(f"invalid amount or quantity: {line!r}")\n    return {\n        "order_id": parts[0],\n        "sku": parts[1],\n        "customer": parts[2],\n        "unit_cents": unit_cents,\n        "qty": qty,\n    }\n',
    ),
    (
        "orders/parser.py",
        'def parse_orders(text):\n    orders = []\n    for line in text.splitlines():\n        if not line.strip():\n            continue\n        orders.append(parse_line(line))\n    return orders\n',
        'def parse_orders(text):\n    orders = {}\n    for line in text.splitlines():\n        if not line.strip() or line.lstrip().startswith("#"):\n            continue\n        order = parse_line(line)\n        orders[order["order_id"]] = order\n    return list(orders.values())\n',
    ),
    (
        "orders/pricing.py",
        "    return total_cents * (100 - config.DISCOUNT_PCT) // 100\n",
        "    return (total_cents * (100 - config.DISCOUNT_PCT) + 50) // 100\n",
    ),
    (
        "orders/pricing.py",
        "    return total_cents * config.TAX_PCT // 100\n",
        "    return (total_cents * config.TAX_PCT + 50) // 100\n",
    ),
    (
        "orders/report.py",
        '    return [\n        row\n        for row in sorted(rows.values(), key=lambda item: item["customer"])\n        if row["net_cents"] != 0\n    ]\n',
        '    return sorted(\n        rows.values(), key=lambda item: (-item["net_cents"], item["customer"])\n    )\n',
    ),
)

VIP_POOL = ("acme", "globex", "initech", "umbrella", "hooli")


@dataclass(frozen=True)
class Case:
    task_id: str
    workdir: Path
    prompt: str
    reference: str
    fixes: tuple[tuple[str, str, str], ...]


def materialize(instance: int, root: Path) -> Case:
    task_id = f"e-{instance:02d}"
    workdir = root / task_id
    if workdir.exists():
        shutil.rmtree(workdir)
    (workdir / "orders").mkdir(parents=True)
    vip = VIP_POOL[instance % len(VIP_POOL)]
    config_source = (
        f'"""Instance configuration."""\n\n'
        f'DISCOUNT_PCT = {10 + (instance % 4) * 3}\n'
        f'TAX_PCT = {5 + (instance % 5)}\n'
        f'VIP_CUSTOMERS = {{"{vip}"}}\n'
    )
    files = {
        "SPEC.md": SPEC,
        "config.py": config_source,
        "orders/__init__.py": "",
        "orders/parser.py": PARSER,
        "orders/pricing.py": PRICING,
        "orders/report.py": REPORT,
        "test_orders.py": PUBLIC_TEST,
    }
    for name, content in files.items():
        (workdir / name).write_text(content, encoding="utf-8")
    prompt = (
        "You are in a small Python repository. Read SPEC.md, run "
        "`python3 -m unittest test_orders.py -v`, and fix every defect so the "
        "public test passes and the implementation matches SPEC.md. Do not "
        "change the public test, do not create new files, and keep the module "
        "layout. Hidden tests check the same specification."
    )
    return Case(
        task_id=task_id,
        workdir=workdir,
        prompt=prompt,
        reference="fixed",
        fixes=FIXES,
    )


def _apply_fixes(directory: Path, fixes: tuple[tuple[str, str, str], ...]) -> None:
    for relative, old, new in fixes:
        path = directory / relative
        source = path.read_text(encoding="utf-8")
        if old not in source:
            raise ValueError(f"fix anchor missing in {relative}")
        path.write_text(source.replace(old, new, 1), encoding="utf-8")


def _run_hidden(workdir: Path) -> tuple[int, int]:
    hidden = workdir / "_hidden_tests.py"
    hidden.write_text(HIDDEN_TESTS, encoding="utf-8")
    try:
        completed = subprocess.run(
            ["python3", "-m", "unittest", "-v", "_hidden_tests"],
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    finally:
        hidden.unlink(missing_ok=True)
    output = completed.stdout + completed.stderr
    passes = len(re.findall(r"\.\.\. ok$", output, re.M))
    failures = len(re.findall(r"\.\.\. (?:FAIL|ERROR)$", output, re.M))
    total = passes + failures
    if completed.returncode != 0 and total == 0:
        return 0, 7
    return passes, total


def verify(case: Case, _output: str) -> dict[str, Any]:
    passes, total = _run_hidden(case.workdir)
    return {"success": passes == total and total > 0, "score": passes / total if total else 0.0}


def dry_run_verify(case: Case) -> dict[str, Any]:
    """Apply the reference fixes to a copy and prove the verifier turns green."""
    with tempfile.TemporaryDirectory() as directory:
        copy = Path(directory) / "repo"
        shutil.copytree(case.workdir, copy)
        _apply_fixes(copy, case.fixes)
        fixed = dataclasses.replace(case, workdir=copy)
        return verify(fixed, "")
