"""Fetch a small OOLONG-subset into the repository-local cache.

OOLONG (oolongbench/oolong-synth) stores each context-length band in its own
parquet shard. We stream two shards (32k and 131k tokens) and keep the first
examples with distinct task types, so the subset exercises aggregation rather
than a single template. The cache is not committed; the family module reads it
and fails with instructions when it is missing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

BASE = "https://huggingface.co/datasets/oolongbench/oolong-synth/resolve/main/data/test-{:05d}-of-00041.parquet"
CACHE = Path(__file__).resolve().parents[3] / ".toolchain" / "oolong" / "subset.json"
WANTED = {32768: 6, 131072: 6}


def main() -> int:
    os.environ.setdefault("HF_HOME", str(CACHE.parents[1] / "hf"))
    from datasets import load_dataset

    shards = {32768: 25, 131072: 5}
    picked: list[dict] = []
    for context_len, count in WANTED.items():
        url = BASE.format(shards[context_len])
        stream = load_dataset("parquet", data_files=url, split="train", streaming=True)
        seen_tasks: set[str] = set()
        taken = 0
        for row in stream:
            if row["context_len"] != context_len:
                continue
            task = row["task"]
            if task in seen_tasks:
                continue
            seen_tasks.add(task)
            picked.append(
                {
                    "id": row["id"],
                    "context_len": row["context_len"],
                    "dataset": row["dataset"],
                    "task": task,
                    "task_group": row["task_group"],
                    "answer_type": row["answer_type"],
                    "context": row["context_window_text"],
                    "question": row["question"],
                    "answer": row["answer"],
                }
            )
            taken += 1
            if taken == count:
                break
        print(f"context_len {context_len}: collected {taken}")
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(picked, indent=1), encoding="utf-8")
    print("wrote", CACHE, "examples", len(picked))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
