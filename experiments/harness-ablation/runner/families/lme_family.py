"""External validity for M: a LongMemEval subset of long-term memory questions.

LongMemEval (Wu et al., ICLR 2025) provides timestamped chat histories and
questions about facts spread across sessions. We take ten questions, split each
history into two reading episodes, and ask the question in a third episode
without the histories: only state the agent stored during the reading episodes
can answer it.

M-on can persist notes in the harness (and may also write files, since its
episode root is shared); M-off gets a fresh HOME, TMPDIR, and session directory
per episode and keeps nothing. Scoring is normalized containment of the
reference answer, which is a stricter than a judge but adequate for the short
oracle answers.

The 15 MB data file is not committed; fetch it once with

    mkdir -p .toolchain/longmemeval
    curl -fL -o .toolchain/longmemeval/longmemeval_oracle.json \\
      https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main/longmemeval_oracle.json
"""

from __future__ import annotations

import json
import re
import string
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).resolve().parents[4] / ".toolchain" / "longmemeval" / "longmemeval_oracle.json"
INSTANCES = 10


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
    question_type: str
    episodes: list[Episode]


def _rows() -> list[dict[str, Any]]:
    if not DATA_PATH.is_file():
        raise FileNotFoundError(
            f"LongMemEval data not found at {DATA_PATH}; see the module docstring"
        )
    rows = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    rows.sort(key=lambda row: row["question_id"])
    # Two questions per question type, deterministic.
    by_type: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_type.setdefault(row["question_type"], []).append(row)
    picked: list[dict[str, Any]] = []
    for question_type in sorted(by_type):
        picked.extend(by_type[question_type][:2])
    return picked[:INSTANCES]


def _render(session: list[dict[str, Any]]) -> str:
    lines = []
    for turn in session:
        role = turn.get("role", "user")
        content = turn.get("content", "")
        lines.append(f"{role}: {content}")
    return "\n".join(lines)


def _normalize(text: str) -> str:
    lowered = text.lower()
    stripped = lowered.translate(str.maketrans("", "", string.punctuation))
    return re.sub(r"\s+", " ", stripped).strip()


def materialize(instance: int, root: Path) -> Case:
    rows = _rows()
    row = rows[(instance - 1) % len(rows)]
    task_id = f"lme-{instance:02d}"
    sessions = row["haystack_sessions"]
    dates = row.get("haystack_dates", [""] * len(sessions))
    split = max(1, len(sessions) // 2)
    halves = (sessions[:split], sessions[split:])
    dates_split = (dates[:split], dates[split:])
    episodes = []
    for index, (half, half_dates) in enumerate(zip(halves, dates_split)):
        directory = root / task_id / f"read{index + 1}"
        directory.mkdir(parents=True, exist_ok=True)
        transcript = "\n\n".join(
            f"[session {date}]\n{_render(session)}"
            for date, session in zip(half_dates, half)
        )
        (directory / "sessions.txt").write_text(transcript, encoding="utf-8")
        prompt = (
            "Read sessions.txt in the current directory: timestamped chat "
            "sessions between a user and an assistant. Extract every durable "
            "fact about the user that a later question might ask about (names, "
            "dates, purchases, preferences, changes over time). Store what you "
            "learn as durable harness memory, because the file will not be "
            "available in later sessions."
        )
        episodes.append(
            Episode(
                name=f"read{index + 1}",
                workdir=directory,
                prompt=prompt,
                reference="",
                scored=False,
            )
        )
    qa_dir = root / task_id / "qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    answer = str(row["answer"])
    qa_prompt = (
        "Answer the question from the durable memory you recorded earlier in "
        "this series. The session transcripts are not available now.\n"
        f"Question: {row['question']}\n"
        "Answer with a short phrase and nothing else."
    )
    episodes.append(
        Episode(
            name="qa",
            workdir=qa_dir,
            prompt=qa_prompt,
            reference=answer,
            scored=True,
        )
    )
    return Case(
        task_id=task_id,
        question_type=row["question_type"],
        episodes=episodes,
    )


def verify_episode(case: Case, index: int, output: str) -> dict[str, Any]:
    episode = case.episodes[index]
    if not episode.scored:
        return {"success": True, "score": 1.0}
    expected = _normalize(episode.reference)
    actual = _normalize(output)
    matched = bool(expected) and (
        expected in actual or (actual and actual in expected and len(actual) > 2)
    )
    return {
        "success": matched,
        "score": 1.0 if matched else 0.0,
        "expected": episode.reference,
        "actual": output.strip()[:200],
        "question_type": case.question_type,
    }


def dry_run_verify(case: Case) -> dict[str, Any]:
    scored = [ep for ep in case.episodes if ep.scored]
    if len(scored) != 1:
        return {"success": False, "score": 0.0}
    result = verify_episode(case, len(case.episodes) - 1, scored[0].reference)
    return {"success": result["success"], "score": result["score"]}
