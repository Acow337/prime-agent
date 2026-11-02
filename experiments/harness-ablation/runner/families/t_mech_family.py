"""Mechanism check: transfer when the rule cannot be guessed from the data.

The main T templates use round thresholds (5%, 800 ms) that a strong model can
plausibly guess without any memory, which makes M-off look as good as M-on. This
family uses arbitrary thresholds derived from the instance (e.g. 713 basis
points and 737 ms), states them once in episode 1, and never repeats them.

The runner also gives M-off episodes a fresh HOME and TMPDIR, so an agent cannot
smuggle notes through files instead of the harness.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from families import t_family

ERROR_VALUES = (40, 180, 305, 320, 480, 690, 715, 900, 1150)
LATENCY_VALUES = (120, 300, 430, 450, 620, 740, 800, 1000, 1300)
EP1_ROWS = 5
EP2_ROWS = 10
EP3_ROWS = 10


@dataclass(frozen=True)
class Thresholds:
    high_error: int
    high_latency: int
    low_error: int
    low_latency: int


def _thresholds(instance: int) -> Thresholds:
    return Thresholds(
        high_error=713 + instance * 3,
        high_latency=737 + instance * 7,
        low_error=311 + instance,
        low_latency=437 + instance * 5,
    )


def _label(row: dict[str, Any], thresholds: Thresholds) -> dict[str, str]:
    if row["error_bp"] >= thresholds.high_error and row["latency_ms"] >= thresholds.high_latency:
        return {"bucket": "red", "action": "hold"}
    if row["error_bp"] >= thresholds.low_error or row["latency_ms"] >= thresholds.low_latency:
        return {"bucket": "amber", "action": "check"}
    return {"bucket": "green", "action": "ship"}


def _rule_text(thresholds: Thresholds) -> str:
    return (
        f"red (action hold): error_bp >= {thresholds.high_error} AND "
        f"latency_ms >= {thresholds.high_latency}\n"
        f"amber (action check): error_bp >= {thresholds.low_error} OR "
        f"latency_ms >= {thresholds.low_latency}\n"
        "green (action ship): everything else"
    )


def _rows(instance: int, episode: int, count: int) -> list[dict[str, Any]]:
    rows = []
    for index in range(count):
        rows.append(
            {
                "id": f"R-{index + 1:03d}",
                "error_bp": ERROR_VALUES[(instance + episode * 3 + index) % len(ERROR_VALUES)],
                "latency_ms": LATENCY_VALUES[(instance * 2 + episode + index) % len(LATENCY_VALUES)],
            }
        )
    return rows


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("id", "error_bp", "latency_ms"))
        writer.writeheader()
        writer.writerows(rows)


OUTPUT_SPEC = (
    'Answer with ONE JSON object and nothing else: {"items": {"<row id>": '
    '{"bucket": "red|amber|green", "action": "hold|check|ship"}, ...}} with one '
    "entry per row.\n"
)


def materialize(instance: int, root: Path) -> t_family.Case:
    thresholds = _thresholds(instance)
    task_id = f"tm-{instance:02d}"
    episodes = []
    specs = (
        (
            "ep1",
            EP1_ROWS,
            "You are starting a recurring grading series. Read rows.csv and label "
            "every row.\nRule:\n"
            + _rule_text(thresholds)
            + "\n"
            + OUTPUT_SPEC
            + "If your tools support durable harness notes, record this exact rule "
            "as a memory before finishing; later episodes will not repeat it.",
            False,
        ),
        (
            "ep2",
            EP2_ROWS,
            "This is episode 2 of the same grading series. Read rows.csv and label "
            "every row using the exact convention recorded in episode 1. The rule "
            "text is deliberately not repeated and cannot be guessed from the data.\n"
            + OUTPUT_SPEC,
            True,
        ),
        (
            "ep3",
            EP3_ROWS,
            "This is episode 3 of the same grading series. Read rows.csv and label "
            "every row using the exact convention recorded in episode 1. The rule "
            "text is deliberately not repeated and cannot be guessed from the data.\n"
            + OUTPUT_SPEC,
            True,
        ),
    )
    for index, (name, count, prompt, scored) in enumerate(specs):
        rows = _rows(instance, index, count)
        directory = root / task_id / name
        directory.mkdir(parents=True, exist_ok=True)
        _write(directory / "rows.csv", rows)
        episodes.append(
            t_family.Episode(
                name=name,
                workdir=directory,
                prompt=prompt,
                reference=json.dumps(
                    {"items": {row["id"]: _label(row, thresholds) for row in rows}},
                    sort_keys=True,
                ),
                scored=scored,
            )
        )
    return t_family.Case(
        task_id=task_id,
        template="mech",
        negative=False,
        episodes=episodes,
    )


def verify_episode(case: t_family.Case, index: int, output: str) -> dict[str, Any]:
    return t_family.verify_episode(case, index, output)


def dry_run_verify(case: t_family.Case) -> dict[str, Any]:
    return t_family.dry_run_verify(case)
