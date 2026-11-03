"""Boundary-row transfer: the accuracy counterpart to the mechanism check.

The rows sit within a few units of the decision thresholds, so a guessed rule
(5%, 800 ms, or any approximation) mislabels them. Only an agent that carries
the exact convention can score; this is the condition under which durable state
should convert into accuracy rather than only into savings.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from families import t_mech_family

EP1_ROWS = 6
EP2_ROWS = 10
EP3_ROWS = 10

OUTPUT_SPEC = (
    'Answer with ONE JSON object and nothing else: {"items": {"<row id>": '
    '{"bucket": "red|amber|green", "action": "hold|check|ship"}, ...}} with one '
    "entry per row.\n"
)


def _rows(instance: int, episode: int, count: int) -> list[dict[str, Any]]:
    t = t_mech_family._thresholds(instance)
    shift = (episode + instance) % 3
    # Each pattern sits one or two units from a threshold, so an approximate
    # rule flips at least one label.
    patterns = [
        (t.high_error - 2, t.high_latency - 2),
        (t.high_error, t.high_latency - 2),
        (t.high_error - 2, t.high_latency),
        (t.high_error, t.high_latency),
        (t.low_error - 2, t.low_latency - 2),
        (t.low_error, t.low_latency - 2),
        (t.low_error - 2, t.low_latency),
        (t.low_error + shift - 1, t.low_latency + shift - 1),
        (t.high_error - shift - 1, t.high_latency - shift - 1),
        (t.high_error + 1, t.high_latency + 1),
    ]
    return [
        {"id": f"R-{index + 1:03d}", "error_bp": patterns[index][0], "latency_ms": patterns[index][1]}
        for index in range(count)
    ]


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("id", "error_bp", "latency_ms"))
        writer.writeheader()
        writer.writerows(rows)


def materialize(instance: int, root: Path) -> t_mech_family.t_family.Case:
    thresholds = t_mech_family._thresholds(instance)
    task_id = f"tb-{instance:02d}"
    specs = (
        ("ep1", EP1_ROWS, False),
        ("ep2", EP2_ROWS, True),
        ("ep3", EP3_ROWS, True),
    )
    episodes = []
    for index, (name, count, scored) in enumerate(specs):
        rows = _rows(instance, index, count)
        directory = root / task_id / name
        directory.mkdir(parents=True, exist_ok=True)
        _write(directory / "rows.csv", rows)
        if index == 0:
            prompt = (
                "You are starting a recurring grading series. Read rows.csv and label "
                "every row using this exact rule:\n"
                + t_mech_family._rule_text(thresholds)
                + "\n"
                + OUTPUT_SPEC
                + "Record this exact rule as durable harness memory before finishing; "
                "later episodes will not repeat it and the rows sit on the boundaries."
            )
        else:
            prompt = (
                f"This is episode {index + 1} of the same grading series. Read rows.csv "
                "and label every row using the exact convention recorded in episode 1. "
                "The thresholds are not repeated, and rows are deliberately within a "
                "few basis points of them.\n" + OUTPUT_SPEC
            )
        episodes.append(
            t_mech_family.t_family.Episode(
                name=name,
                workdir=directory,
                prompt=prompt,
                reference=json.dumps(
                    {"items": {row["id"]: t_mech_family._label(row, thresholds) for row in rows}},
                    sort_keys=True,
                ),
                scored=scored,
            )
        )
    return t_mech_family.t_family.Case(
        task_id=task_id,
        template="boundary",
        negative=False,
        episodes=episodes,
    )


def verify_episode(case: Any, index: int, output: str) -> dict[str, Any]:
    return t_mech_family.verify_episode(case, index, output)


def dry_run_verify(case: Any) -> dict[str, Any]:
    return t_mech_family.dry_run_verify(case)
