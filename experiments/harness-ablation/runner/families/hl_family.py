"""Hidden-policy series with a controllable episode count.

Instance layout: one policy episode states an arbitrary code-to-queue mapping
(12 codes, 3 deprecated) and asks for a durable memory; the following N-1
episodes each hand over a small ticket batch that contains only the codes. The
mapping never appears in a file the later episodes can read, so an agent without
durable state cannot route tickets above chance.

Instances 1-4 use 3 episodes, 5-8 use 6, 9-12 use 12, so the same mechanism is
measured across series lengths.
"""

from __future__ import annotations

import csv
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

QUEUES = ("KAPPA", "DELTA", "OMEGA", "SIGMA", "THETA", "ZETA", "LAMBDA", "RHO", "MU", "NU")
DEPRECATED = "REJECT"
SEEDS_PER_LENGTH = 4
LENGTHS = (3, 6, 12)
TICKETS = 6


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
    episodes: list[Episode]


def _layout(instance: int) -> tuple[int, int]:
    index = instance - 1
    length = LENGTHS[index // SEEDS_PER_LENGTH]
    seed = index % SEEDS_PER_LENGTH
    return length, seed


def _mapping(length: int, seed: int) -> dict[str, str]:
    rng = random.Random(3000 + length * 10 + seed)
    codes = [f"CX-{index:02d}" for index in range(1, 13)]
    mapping = {code: rng.choice(QUEUES) for code in codes}
    for code in rng.sample(codes, 3):
        mapping[code] = DEPRECATED
    return mapping


def _tickets(length: int, seed: int, episode: int, mapping: dict[str, str]) -> list[dict[str, str]]:
    rng = random.Random(4000 + length * 100 + seed * 10 + episode)
    codes = list(mapping)
    return [
        {"ticket_id": f"T-{episode}-{index + 1:02d}", "code": rng.choice(codes)}
        for index in range(TICKETS)
    ]


def _write(path: Path, tickets: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("ticket_id", "code"))
        writer.writeheader()
        writer.writerows(tickets)


def materialize(instance: int, root: Path) -> Case:
    length, seed = _layout(instance)
    mapping = _mapping(length, seed)
    task_id = f"hl-{length}-{seed}"
    table = "\n".join(f"{code} -> {queue}" for code, queue in mapping.items())
    policy_dir = root / task_id / "policy"
    policy_dir.mkdir(parents=True, exist_ok=True)
    (policy_dir / "policy.txt").write_text(table, encoding="utf-8")
    episodes = [
        Episode(
            name="policy",
            workdir=policy_dir,
            prompt=(
                "Read policy.txt and record this exact mapping as durable harness "
                "memory. It is the only place the mapping is ever stated; the ticket "
                "episodes that follow will contain only the codes.\n"
                + table
                + "\nCodes mapping to REJECT are deprecated and must be rejected."
            ),
            reference="",
            scored=False,
        )
    ]
    for number in range(1, length):
        directory = root / task_id / f"tickets-{number:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        tickets = _tickets(length, seed, number, mapping)
        _write(directory / "tickets.csv", tickets)
        reference = json.dumps(
            {"items": {ticket["ticket_id"]: mapping[ticket["code"]] for ticket in tickets}},
            sort_keys=True,
        )
        episodes.append(
            Episode(
                name=f"tickets-{number:02d}",
                workdir=directory,
                prompt=(
                    "Read tickets.csv and route every ticket using the mapping recorded "
                    "in the first episode. The mapping is not in any file and cannot be "
                    "derived from the codes.\n"
                    'Answer with ONE JSON object: {"items": {"<ticket id>": "<queue>"}}.'
                ),
                reference=reference,
                scored=True,
            )
        )
    return Case(task_id=task_id, episodes=episodes)


def verify_episode(case: Case, index: int, output: str) -> dict[str, Any]:
    episode = case.episodes[index]
    if not episode.scored:
        return {"success": True, "score": 1.0}
    expected = json.loads(episode.reference)["items"]
    try:
        parsed = json.loads(output.strip())["items"]
        if not isinstance(parsed, dict):
            raise ValueError("items is not an object")
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {"success": False, "score": 0.0, "expected": episode.reference, "actual": output[:200]}
    correct = sum(
        1
        for ticket_id, queue in expected.items()
        if str(parsed.get(ticket_id, "")).strip().upper() == queue
    )
    score = correct / len(expected)
    return {
        "success": score == 1.0,
        "score": score,
        "expected": episode.reference,
        "actual": output[:300],
        "correct": correct,
        "total": len(expected),
    }


def dry_run_verify(case: Case) -> dict[str, Any]:
    first = next((index for index, episode in enumerate(case.episodes) if episode.scored), None)
    if first is None:
        return {"success": False, "score": 0.0}
    return verify_episode(case, first, case.episodes[first].reference)
