"""T-family pilot tasks: cross-episode transfer of a classification convention.

Each instance is one template of a recurring series:

1. episode 1 states the rule and asks for a durable harness note (not scored),
2. episode 2 withholds the rule and scores transfer,
3. episode 3 is scored too. Half the templates keep the rule (positive
   transfer); the other half change one threshold (negative transfer), and a
   session that blindly reuses the stored rule loses points.

Ten templates differ in the domain, feature columns, thresholds, label names,
and routing vocabulary, so the series is a class of tasks rather than one task
with new numbers. Instances 1-30 map to template ``(n-1) % 10`` with a variant
counter ``(n-1) // 10`` feeding the seed.
"""

from __future__ import annotations

import csv
import json
import random
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

EP1_ROWS = 4
EP2_ROWS = 10
EP3_ROWS = 10


@dataclass(frozen=True)
class Template:
    name: str
    entity: str
    columns: dict[str, tuple[str, Any]]
    label: Callable[[dict[str, Any], dict[str, Any]], dict[str, str]]
    params: dict[str, Any]
    changed: dict[str, Any]
    rules_text: str
    change_text: str
    negative: bool


def _triage(row, p):
    if row["error_rate_pct"] >= p["critical_err"] and row["latency_ms"] >= p["critical_lat"]:
        return {"severity": "critical", "route": "oncall"}
    if row["error_rate_pct"] >= p["major_err"] or row["latency_ms"] >= p["major_lat"]:
        return {"severity": "major", "route": "queue"}
    return {"severity": "minor", "route": "queue"}


def _sla(row, p):
    if row["latency_ms"] >= p["breach_lat"] or row["error_rate_pct"] >= p["breach_err"]:
        return {"status": "breach", "action": "escalate"}
    if row["latency_ms"] >= p["risk_lat"] or row["error_rate_pct"] >= p["risk_err"]:
        return {"status": "at_risk", "action": "watch"}
    return {"status": "ok", "action": "none"}


def _risk(row, p):
    if row["amount"] >= p["high_amount"] and row["age_days"] >= p["high_age"]:
        return {"band": "high", "action": "review"}
    if row["amount"] >= p["mid_amount"] or row["age_days"] >= p["mid_age"]:
        return {"band": "medium", "action": "hold"}
    return {"band": "low", "action": "accept"}


def _capacity(row, p):
    if row["cpu_pct"] >= p["over_cpu"] and row["mem_pct"] >= p["over_mem"]:
        return {"load": "overload", "action": "scale"}
    if row["cpu_pct"] >= p["high_cpu"] or row["mem_pct"] >= p["high_mem"]:
        return {"load": "high", "action": "monitor"}
    return {"load": "normal", "action": "none"}


def _fraud(row, p):
    if row["velocity"] >= p["block_vel"] and row["amount"] >= p["block_amount"]:
        return {"verdict": "block", "route": "analyst"}
    if row["velocity"] >= p["review_vel"] or row["amount"] >= p["review_amount"]:
        return {"verdict": "review", "route": "queue"}
    return {"verdict": "allow", "route": "auto"}


def _quality(row, p):
    if row["null_pct"] >= p["quarantine_null"] or row["dup_pct"] >= p["quarantine_dup"]:
        return {"grade": "quarantine", "owner": "data"}
    if row["null_pct"] >= p["warn_null"]:
        return {"grade": "warn", "owner": "data"}
    return {"grade": "pass", "owner": "none"}


def _routing(row, p):
    if row["revenue"] >= p["vip_revenue"] and row["region"] == p["vip_region"]:
        return {"tier": "vip", "desk": "named"}
    if row["revenue"] >= p["standard_revenue"]:
        return {"tier": "standard", "desk": "shared"}
    return {"tier": "basic", "desk": "self"}


def _retention(row, p):
    if row["days_inactive"] >= p["churn_days"] and row["tickets"] >= p["churn_tickets"]:
        return {"state": "churn_risk", "play": "save"}
    if row["days_inactive"] >= p["risk_days"] or row["tickets"] >= p["risk_tickets"]:
        return {"state": "at_risk", "play": "nurture"}
    return {"state": "healthy", "play": "none"}


def _escalation(row, p):
    if row["impact"] >= p["p1_impact"] and row["urgency"] >= p["p1_urgency"]:
        return {"priority": "p1", "channel": "page"}
    if row["impact"] >= p["p2_impact"] or row["urgency"] >= p["p2_urgency"]:
        return {"priority": "p2", "channel": "ticket"}
    return {"priority": "p3", "channel": "backlog"}


def _compliance(row, p):
    if row["missing_docs"] >= p["fail_docs"] and row["risk_score"] >= p["fail_risk"]:
        return {"outcome": "fail", "owner": "legal"}
    if row["missing_docs"] >= p["cond_docs"] or row["risk_score"] >= p["cond_risk"]:
        return {"outcome": "conditional", "owner": "legal"}
    return {"outcome": "pass", "owner": "none"}


TEMPLATES = (
    Template(
        "triage",
        "incident",
        {
            "latency_ms": ("choice", (120, 350, 480, 520, 700, 820, 950, 1200)),
            "error_rate_pct": ("choice", (0, 1, 2, 3, 4, 5, 6, 9, 12)),
        },
        _triage,
        {"critical_err": 5, "critical_lat": 800, "major_err": 2, "major_lat": 500},
        {"critical_err": 8},
        "critical (route oncall): error_rate_pct >= 5 AND latency_ms >= 800\n"
        "major (route queue): error_rate_pct >= 2 OR latency_ms >= 500\n"
        "minor (route queue): everything else",
        "critical now requires error_rate_pct >= 8 AND latency_ms >= 800; the major rule is unchanged",
        True,
    ),
    Template(
        "sla",
        "endpoint",
        {
            "latency_ms": ("choice", (200, 450, 610, 700, 880, 950, 1100)),
            "error_rate_pct": ("choice", (0, 1, 2, 3, 4, 5, 7)),
        },
        _sla,
        {"breach_lat": 900, "breach_err": 4, "risk_lat": 600, "risk_err": 2},
        {},
        "breach (action escalate): latency_ms >= 900 OR error_rate_pct >= 4\n"
        "at_risk (action watch): latency_ms >= 600 OR error_rate_pct >= 2\n"
        "ok (action none): everything else",
        "the SLA policy is unchanged",
        False,
    ),
    Template(
        "risk",
        "account",
        {
            "amount": ("choice", (5000, 12000, 24000, 31000, 52000, 80000)),
            "age_days": ("choice", (3, 12, 18, 30, 45, 90)),
        },
        _risk,
        {"high_amount": 50000, "high_age": 30, "mid_amount": 20000, "mid_age": 15},
        {},
        "high (action review): amount >= 50000 AND age_days >= 30\n"
        "medium (action hold): amount >= 20000 OR age_days >= 15\n"
        "low (action accept): everything else",
        "the risk bands are unchanged",
        False,
    ),
    Template(
        "capacity",
        "node",
        {
            "cpu_pct": ("choice", (30, 55, 70, 76, 85, 92)),
            "mem_pct": ("choice", (40, 65, 80, 88, 93)),
        },
        _capacity,
        {"over_cpu": 85, "over_mem": 90, "high_cpu": 70, "high_mem": 80},
        {"over_cpu": 90},
        "overload (action scale): cpu_pct >= 85 AND mem_pct >= 90\n"
        "high (action monitor): cpu_pct >= 70 OR mem_pct >= 80\n"
        "normal (action none): everything else",
        "overload now requires cpu_pct >= 90 AND mem_pct >= 90; the high rule is unchanged",
        True,
    ),
    Template(
        "fraud",
        "payment",
        {
            "velocity": ("choice", (1, 3, 4, 6, 8, 11)),
            "amount": ("choice", (50, 300, 520, 900, 1400, 5000)),
        },
        _fraud,
        {"block_vel": 8, "block_amount": 1000, "review_vel": 4, "review_amount": 500},
        {},
        "block (route analyst): velocity >= 8 AND amount >= 1000\n"
        "review (route queue): velocity >= 4 OR amount >= 500\n"
        "allow (route auto): everything else",
        "the fraud policy is unchanged",
        False,
    ),
    Template(
        "quality",
        "dataset",
        {
            "null_pct": ("choice", (0, 2, 3, 6, 11, 15)),
            "dup_pct": ("choice", (0, 1, 3, 5, 6, 9)),
        },
        _quality,
        {"quarantine_null": 10, "quarantine_dup": 5, "warn_null": 3},
        {"quarantine_dup": 8},
        "quarantine (owner data): null_pct >= 10 OR dup_pct >= 5\n"
        "warn (owner data): null_pct >= 3\n"
        "pass (owner none): everything else",
        "quarantine now requires null_pct >= 10 OR dup_pct >= 8",
        True,
    ),
    Template(
        "routing",
        "customer",
        {
            "revenue": ("choice", (10000, 40000, 52000, 80000, 120000)),
            "region": ("choice", ("AMER", "APAC", "EMEA")),
        },
        _routing,
        {"vip_revenue": 100000, "vip_region": "EMEA", "standard_revenue": 50000},
        {},
        "vip (desk named): revenue >= 100000 AND region == EMEA\n"
        "standard (desk shared): revenue >= 50000\n"
        "basic (desk self): everything else",
        "the routing policy is unchanged",
        False,
    ),
    Template(
        "retention",
        "account",
        {
            "days_inactive": ("choice", (5, 20, 31, 45, 60, 90)),
            "tickets": ("choice", (0, 1, 2, 3, 5)),
        },
        _retention,
        {"churn_days": 60, "churn_tickets": 3, "risk_days": 30, "risk_tickets": 2},
        {},
        "churn_risk (play save): days_inactive >= 60 AND tickets >= 3\n"
        "at_risk (play nurture): days_inactive >= 30 OR tickets >= 2\n"
        "healthy (play none): everything else",
        "the retention policy is unchanged",
        False,
    ),
    Template(
        "escalation",
        "ticket",
        {
            "impact": ("choice", (1, 4, 6, 7, 9, 10)),
            "urgency": ("choice", (1, 3, 6, 7, 8, 9)),
        },
        _escalation,
        {"p1_impact": 9, "p1_urgency": 8, "p2_impact": 6, "p2_urgency": 6},
        {"p1_impact": 10},
        "p1 (channel page): impact >= 9 AND urgency >= 8\n"
        "p2 (channel ticket): impact >= 6 OR urgency >= 6\n"
        "p3 (channel backlog): everything else",
        "p1 now requires impact >= 10 AND urgency >= 8; the p2 rule is unchanged",
        True,
    ),
    Template(
        "compliance",
        "vendor",
        {
            "missing_docs": ("choice", (0, 1, 2, 3)),
            "risk_score": ("choice", (10, 30, 45, 60, 75, 90)),
        },
        _compliance,
        {"fail_docs": 2, "fail_risk": 70, "cond_docs": 1, "cond_risk": 40},
        {},
        "fail (owner legal): missing_docs >= 2 AND risk_score >= 70\n"
        "conditional (owner legal): missing_docs >= 1 OR risk_score >= 40\n"
        "pass (owner none): everything else",
        "the compliance policy is unchanged",
        False,
    ),
)

def _output_spec(labels: dict[str, str]) -> str:
    example = json.dumps(labels)
    return (
        'Answer with ONE JSON object and nothing else: '
        '{"items": {"<row id>": ' + example + ', ...}} '
        "with one entry per row, using exactly these field names.\n"
    )


@dataclass(frozen=True)
class Episode:
    name: str
    workdir: Path
    prompt: str
    reference: str
    scored: bool


@dataclass(frozen=True)
class Case:
    task_id: str
    template: str
    negative: bool
    episodes: list[Episode]


def _rows(template: Template, seed: int, count: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    rows = []
    for index in range(count):
        row: dict[str, Any] = {"id": f"R-{index + 1:03d}"}
        for column, (kind, values) in template.columns.items():
            row[column] = rng.choice(values) if kind == "choice" else rng.randint(*values)
        rows.append(row)
    return rows


def _expected(template: Template, rows: list[dict[str, Any]], params: dict[str, Any]) -> dict[str, Any]:
    return {
        row["id"]: template.label(row, params)
        for row in rows
    }


def _json(expected: dict[str, Any]) -> str:
    return json.dumps({"items": expected}, sort_keys=True)


def _write_rows(path: Path, rows: list[dict[str, Any]], template: Template) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("id", *template.columns))
        writer.writeheader()
        writer.writerows(rows)


def _rules_prompt(template: Template, row_name: str, output_spec: str) -> str:
    return (
        f"You are starting a recurring {template.entity} classification series. "
        f"Read {row_name} and label every row.\n"
        f"Rule:\n{template.rules_text}\n"
        f"{output_spec}"
        "If your tools support durable harness notes, record this rule as a memory "
        "before finishing, so later episodes of the series can reuse it."
    )


def materialize(instance: int, root: Path) -> Case:
    template = TEMPLATES[(instance - 1) % len(TEMPLATES)]
    variant = (instance - 1) // len(TEMPLATES)
    seed = 9000 + instance * 10 + variant
    task_id = f"t-{instance:02d}"
    episodes = []
    sample_spec = _output_spec(template.label(_rows(template, seed + 1, 1)[0], template.params))
    specs = (
        ("ep1", EP1_ROWS, template.params, False, _rules_prompt(template, "rows.csv", sample_spec)),
        (
            "ep2",
            EP2_ROWS,
            template.params,
            True,
            "This is episode 2 of the same series. Read rows.csv and label every row "
            "using the convention established in episode 1. The rule text is "
            "deliberately not repeated; reuse your recorded convention. If you have "
            "no durable record of it, derive the best rule you can.\n" + sample_spec,
        ),
        (
            "ep3",
            EP3_ROWS,
            ({**template.params, **template.changed} if template.negative else template.params),
            True,
            (
                "This is episode 3 of the series. "
                + (
                    f"The policy changed: {template.change_text}. Apply the NEW rule and "
                    "ignore any stored rule that contradicts it.\n"
                    if template.negative
                    else "The policy is unchanged. Apply the same convention.\n"
                )
                + sample_spec
            ),
        ),
    )
    for index, (name, count, params, scored, prompt) in enumerate(specs):
        rows = _rows(template, seed + index * 7, count)
        directory = root / task_id / name
        directory.mkdir(parents=True, exist_ok=True)
        _write_rows(directory / "rows.csv", rows, template)
        episodes.append(
            Episode(
                name=name,
                workdir=directory,
                prompt=prompt,
                reference=_json(_expected(template, rows, params)),
                scored=scored,
            )
        )
    return Case(
        task_id=task_id,
        template=template.name,
        negative=template.negative,
        episodes=episodes,
    )


def parse_output(text: str) -> dict[str, Any] | None:
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates += re.findall(r"\{.*\}", text, re.S)
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        items = parsed.get("items") if isinstance(parsed, dict) else None
        if isinstance(items, dict):
            return items
    return None


def verify_episode(case: Case, index: int, output: str) -> dict[str, Any]:
    episode = case.episodes[index]
    expected = json.loads(episode.reference)["items"]
    parsed = parse_output(output)
    details: dict[str, Any] = {"parsed": parsed is not None}
    if parsed is None:
        return {"success": False, "score": 0.0, **details}
    correct = 0
    for item_id, wanted in expected.items():
        actual = parsed.get(item_id)
        if actual == wanted:
            correct += 1
        else:
            details[item_id] = {"expected": wanted, "actual": actual}
    total = len(expected)
    return {
        "success": correct == total,
        "score": correct / total if total else 0.0,
        **details,
    }


def dry_run_verify(case: Case) -> dict[str, Any]:
    """Every episode's reference answer must score perfectly offline."""
    scored = []
    for index, episode in enumerate(case.episodes):
        result = verify_episode(case, index, episode.reference)
        if episode.scored:
            scored.append(result["score"])
        if not result["success"]:
            return {"success": False, "score": 0.0}
    return {"success": True, "score": sum(scored) / len(scored) if scored else 1.0}
