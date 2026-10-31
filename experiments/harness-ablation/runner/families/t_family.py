"""T-family pilot tasks: cross-episode transfer of a triage convention.

One instance is a three-episode series:

1. the triage rule is stated explicitly and the model is asked to record it as
   durable harness memory when its tools allow it (not scored),
2. new incidents, same rule, and the rule text is NOT repeated: a session that
   persisted the convention can reuse it, a stateless one must guess,
3. the critical threshold changes; reusing the stale rule is detected.

Episodes 2 and 3 are scored (fraction of incidents with both fields correct).
"""

from __future__ import annotations

import csv
import json
import random
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

REGIONS = ("AMER", "APAC", "EMEA")
EP1_INCIDENTS = 4
EP2_INCIDENTS = 10
EP3_INCIDENTS = 10


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
    episodes: list[Episode] = field(default_factory=list)


def _rule(latency_ms: int, error_rate_pct: int, critical_error: int) -> dict[str, str]:
    if error_rate_pct >= critical_error and latency_ms >= 800:
        return {"severity": "critical", "route": "oncall"}
    if error_rate_pct >= 2 or latency_ms >= 500:
        return {"severity": "major", "route": "queue"}
    return {"severity": "minor", "route": "queue"}


def _incidents(seed: int, count: int) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    rows = []
    for index in range(count):
        rows.append(
            {
                "incident_id": f"INC-{index + 1:03d}",
                "region": rng.choice(REGIONS),
                "latency_ms": rng.choice((120, 350, 480, 520, 700, 820, 950, 1200)),
                "error_rate_pct": rng.choice((0, 1, 2, 3, 4, 5, 6, 9, 12)),
            }
        )
    return rows


def _expected(rows: list[dict[str, Any]], critical_error: int) -> dict[str, dict[str, str]]:
    return {
        row["incident_id"]: _rule(row["latency_ms"], row["error_rate_pct"], critical_error)
        for row in rows
    }


def _write_incidents(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=("incident_id", "region", "latency_ms", "error_rate_pct")
        )
        writer.writeheader()
        writer.writerows(rows)


def _json(expected: dict[str, dict[str, str]]) -> str:
    return json.dumps({"incidents": expected}, sort_keys=True)


RULES = (
    "Triage rule:\n"
    "- critical (route oncall): error_rate_pct >= 5 AND latency_ms >= 800\n"
    "- major (route queue): error_rate_pct >= 2 OR latency_ms >= 500\n"
    "- minor (route queue): everything else\n"
)

OUTPUT_SPEC = (
    "Answer with ONE JSON object and nothing else:\n"
    '{"incidents": {"<incident_id>": {"severity": "critical|major|minor", '
    '"route": "oncall|queue"}}}\n'
)


def materialize(instance: int, root: Path) -> Case:
    seed = 9000 + instance
    task_id = f"t-{instance:02d}"
    episodes = []

    ep1_rows = _incidents(seed, EP1_INCIDENTS)
    ep2_rows = _incidents(seed + 1, EP2_INCIDENTS)
    ep3_rows = _incidents(seed + 2, EP3_INCIDENTS)

    ep1_dir = root / task_id / "ep1"
    ep1_dir.mkdir(parents=True, exist_ok=True)
    _write_incidents(ep1_dir / "incidents.csv", ep1_rows)
    ep1_prompt = (
        "You are starting a recurring on-call series. Read incidents.csv and label "
        "every incident.\n"
        f"{RULES}\n"
        f"{OUTPUT_SPEC}"
        "If your tools support durable harness notes, record this triage rule as a "
        "memory before finishing, so later episodes of the series can reuse it."
    )
    episodes.append(
        Episode(
            name="ep1",
            workdir=ep1_dir,
            prompt=ep1_prompt,
            reference=_json(_expected(ep1_rows, critical_error=5)),
            scored=False,
        )
    )

    ep2_dir = root / task_id / "ep2"
    ep2_dir.mkdir(parents=True, exist_ok=True)
    _write_incidents(ep2_dir / "incidents.csv", ep2_rows)
    ep2_prompt = (
        "This is episode 2 of the same on-call series. Read incidents.csv and label "
        "every incident using the triage convention established in episode 1. The rule "
        "text is deliberately not repeated; reuse your recorded convention. If you have "
        "no durable record of it, derive the best rule you can.\n"
        f"{OUTPUT_SPEC}"
    )
    episodes.append(
        Episode(
            name="ep2",
            workdir=ep2_dir,
            prompt=ep2_prompt,
            reference=_json(_expected(ep2_rows, critical_error=5)),
            scored=True,
        )
    )

    ep3_dir = root / task_id / "ep3"
    ep3_dir.mkdir(parents=True, exist_ok=True)
    _write_incidents(ep3_dir / "incidents.csv", ep3_rows)
    ep3_prompt = (
        "This is episode 3 of the series. The on-call policy changed: critical now "
        "requires error_rate_pct >= 8 AND latency_ms >= 800. The major rule is "
        "unchanged (error_rate_pct >= 2 OR latency_ms >= 500), and everything else is "
        "minor. Read incidents.csv and label every incident under the NEW rule; ignore "
        "any stored rule that contradicts it.\n"
        f"{OUTPUT_SPEC}"
    )
    episodes.append(
        Episode(
            name="ep3",
            workdir=ep3_dir,
            prompt=ep3_prompt,
            reference=_json(_expected(ep3_rows, critical_error=8)),
            scored=True,
        )
    )
    return Case(task_id=task_id, episodes=episodes)


def parse_output(text: str) -> dict[str, dict[str, str]] | None:
    candidates = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidates += re.findall(r"\{.*\}", text, re.S)
    for candidate in reversed(candidates):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        incidents = parsed.get("incidents") if isinstance(parsed, dict) else None
        if isinstance(incidents, dict):
            return incidents
    return None


def verify_episode(case: Case, index: int, output: str) -> dict[str, Any]:
    episode = case.episodes[index]
    expected = json.loads(episode.reference)["incidents"]
    parsed = parse_output(output)
    details: dict[str, Any] = {"parsed": parsed is not None}
    if parsed is None:
        return {"success": False, "score": 0.0, **details}
    correct = 0
    for incident_id, wanted in expected.items():
        actual = parsed.get(incident_id)
        if actual == wanted:
            correct += 1
        else:
            details[incident_id] = {"expected": wanted, "actual": actual}
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
