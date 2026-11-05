"""External validity for R: the OOLONG long-context aggregation subset.

OOLONG (Tan et al., 2025) asks questions whose answers require aggregating over
a long context (counting, relative frequency, user tracking, numeric
aggregation). The standard input is context_window_text + question. We keep the
context in a file and let the agent choose how to read it: a persistent REPL can
script over the file, a per-turn-reloaded one pays the read cost repeatedly, and
the model's context window is the only other route.

The subset is fetched by
experiments/harness-ablation/external/fetch_oolong_lengths.py into
.toolchain/oolong/subset-lengths.json (one example per context-length
band); it is not committed.
"""

from __future__ import annotations

import ast
import json
import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parents[4] / ".toolchain" / "oolong" / "subset-lengths.json"


@dataclass(frozen=True)
class Case:
    task_id: str
    workdir: Path
    prompt: str
    reference: str
    task: str
    context_len: int


def _rows() -> list[dict[str, Any]]:
    if not DATA_PATH.is_file():
        raise FileNotFoundError(
            f"OOLONG subset not found at {DATA_PATH}; run "
            "python3 experiments/harness-ablation/external/fetch_oolong.py"
        )
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def _answer(value: Any) -> str:
    if isinstance(value, str):
        try:
            parsed = ast.literal_eval(value)
            if isinstance(parsed, list):
                value = parsed[0] if parsed else ""
        except (ValueError, SyntaxError):
            pass
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value)


def _normalize(text: str) -> str:
    lowered = text.lower().strip()
    stripped = lowered.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", stripped).strip()


def materialize(instance: int, root: Path) -> Case:
    rows = _rows()
    row = rows[(instance - 1) % len(rows)]
    task_id = f"ol-{instance:02d}"
    workdir = root / task_id
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "context.txt").write_text(row["context"], encoding="utf-8")
    prompt = (
        "Read context.txt in the current directory. It contains the data for the "
        "question below; the file can be large, so use code to inspect it rather "
        "than reading it all at once if that helps.\n"
        f"Question: {row['question']}\n"
        "Answer with just the final answer, in the form the question requests."
    )
    return Case(
        task_id=task_id,
        workdir=workdir,
        prompt=prompt,
        reference=_answer(row["answer"]),
        task=row["task"],
        context_len=row["context_len"],
    )


def verify(case: Case, output: str) -> dict[str, Any]:
    expected = _normalize(case.reference)
    actual_raw = output.strip()
    actual = _normalize(actual_raw)
    matched = expected == actual or (expected and expected in actual)
    if not matched:
        # numeric answers are often embedded in a sentence
        numbers = re.findall(r"-?\d+", actual_raw.replace(",", ""))
        expected_number = re.findall(r"-?\d+", case.reference.replace(",", ""))
        matched = bool(numbers and expected_number and numbers[-1] == expected_number[0])
    return {
        "success": matched,
        "score": 1.0 if matched else 0.0,
        "expected": case.reference,
        "actual": actual_raw[:300],
        "task": case.task,
        "context_len": case.context_len,
    }


def dry_run_verify(case: Case) -> dict[str, Any]:
    return verify(case, case.reference)
