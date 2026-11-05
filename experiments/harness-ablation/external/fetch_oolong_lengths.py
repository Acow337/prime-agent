"""Build an OOLONG subset with one example per context-length band.

The dataset stores each length band in its own parquet shard. We stream the
first row of every shard to learn the band, then keep the first example (with a
distinct task type where possible) for each band. Output:
.toolchain/oolong/subset-lengths.json
"""

from __future__ import annotations

import json
import os
from pathlib import Path

BASE = "https://huggingface.co/datasets/oolongbench/oolong-synth/resolve/main/data/test-{:05d}-of-00041.parquet"
ROOT = Path(__file__).resolve().parents[3]
CACHE = ROOT / ".toolchain" / "oolong" / "subset-lengths.json"
SHARDS = 41


def main() -> int:
    os.environ.setdefault("HF_HOME", str(ROOT / ".toolchain" / "hf"))
    from datasets import load_dataset

    by_length: dict[int, dict] = {}
    for shard in range(SHARDS):
        url = BASE.format(shard)
        try:
            stream = load_dataset("parquet", data_files=url, split="train", streaming=True)
            row = next(iter(stream))
        except Exception as error:  # noqa: BLE001 - a missing shard should not abort the sweep
            print(f"shard {shard}: {type(error).__name__}")
            continue
        length = int(row["context_len"])
        if length in by_length:
            continue
        by_length[length] = {
            "id": row["id"],
            "context_len": length,
            "dataset": row["dataset"],
            "task": row["task"],
            "task_group": row["task_group"],
            "answer_type": row["answer_type"],
            "context": row["context_window_text"],
            "question": row["question"],
            "answer": row["answer"],
        }
        print(f"shard {shard}: context_len {length} task {row['task']}")
    picked = [by_length[length] for length in sorted(by_length)]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(picked, indent=1), encoding="utf-8")
    print("lengths", sorted(by_length), "examples", len(picked), "->", CACHE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
