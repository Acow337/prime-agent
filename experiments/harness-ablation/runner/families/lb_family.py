"""External validity: a LongBench-v2 subset of public long-context questions.

LongBench-v2 is a public multiple-choice long-context benchmark (503 questions,
contexts from ~8k to ~2M words). We take 20 questions whose context fits the
model's window, present the question and four choices with the context, and
check the answer letter exactly.

The 443 MB dataset file is not committed; fetch it once with

    mkdir -p .toolchain/longbench
    curl -fL -o .toolchain/longbench/data.json \\
      https://huggingface.co/datasets/zai-org/LongBench-v2/resolve/main/data.json
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parents[4] / ".toolchain" / "longbench" / "data.json"
MAX_CONTEXT_CHARS = 180_000
LETTERS = ("A", "B", "C", "D")


@dataclass(frozen=True)
class Case:
    task_id: str
    workdir: Path
    prompt: str
    reference: str
    expected: str
    domain: str
    context_chars: int


_POOL: list[dict[str, Any]] | None = None


def _round_robin(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic spread: take one question per domain in turn."""
    queues: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        queues.setdefault(row["domain"], []).append(row)
    ordered = [sorted(queue, key=lambda item: item["_id"]) for queue in queues.values()]
    pool: list[dict[str, Any]] = []
    while any(ordered):
        for queue in ordered:
            if queue:
                pool.append(queue.pop(0))
    return pool


def _load() -> list[dict[str, Any]]:
    global _POOL
    if _POOL is None:
        if not DATA_PATH.is_file():
            raise FileNotFoundError(
                f"LongBench-v2 data not found at {DATA_PATH}; see the module docstring"
            )
        rows = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        eligible = [row for row in rows if len(row["context"]) <= MAX_CONTEXT_CHARS]
        _POOL = _round_robin(eligible)
    return _POOL


def materialize(instance: int, root: Path) -> Case:
    pool = _load()
    if not pool:
        raise RuntimeError("no LongBench-v2 question fits the context budget")
    row = pool[(instance - 1) % len(pool)]
    task_id = f"lb-{instance:02d}"
    workdir = root / task_id
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "context.txt").write_text(row["context"], encoding="utf-8")
    choices = "\n".join(
        f"{LETTERS[index]}. {row[f'choice_{LETTERS[index]}']}" for index in range(4)
    )
    prompt = (
        "Read context.txt in the current directory and answer the multiple-choice "
        "question using only that document.\n"
        f"Question: {row['question']}\n"
        f"{choices}\n"
        "Answer with exactly one letter (A, B, C, or D) and nothing else."
    )
    answer = str(row["answer"]).strip().upper()
    return Case(
        task_id=task_id,
        workdir=workdir,
        prompt=prompt,
        reference=answer,
        expected=answer,
        domain=row["domain"],
        context_chars=len(row["context"]),
    )


def verify(case: Case, output: str) -> dict[str, Any]:
    match = re.search(r"\b([ABCD])\b", output.upper())
    actual = match.group(1) if match else None
    return {
        "success": actual == case.expected,
        "score": 1.0 if actual == case.expected else 0.0,
        "expected": case.expected,
        "actual": actual,
        "domain": case.domain,
        "context_chars": case.context_chars,
    }
