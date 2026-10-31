# Harness-ablation matrix runner

Runs every enabled `(model, R/S/M treatment, task instance)` cell, verifies the
answer with the task family's verifier, and appends one JSONL record per cell so
an interrupted run resumes without repeating completed cells.

## Usage

```bash
# Offline: materialize every cell and verify the reference answers (no API).
python3 runner.py --config config.pilot.json \
  --binary ../../../target/debug/prime-agent \
  --results results/pilot-dry --mode dry-run

# One live cell (spends a fraction of a cent).
PRIME_AGENT_KERNEL_PYTHON=... python3 runner.py --config config.pilot.json \
  --binary ../../../target/debug/prime-agent \
  --results results/pilot --mode live --limit 1
```

Live mode reads the DeepSeek credential from macOS Keychain
(`prime-agent-deepseek-smoke`), forwards through the bounded loopback proxy from
`../smoke`, sets `PRIME_AGENT_HARNESS_TREATMENT` for each cell, and writes only
`records.jsonl` + `summary.json` under `--results`.

## Matrix

`config.pilot.json` enables the cheap model and the I-family instances 1–5, so a
dry run is 5 × 8 = 40 cells. Adding the E and T families (`families/e`,
`families/t`) to the same config reaches the designed pilot of 3 × 5 × 8 = 120
runs; those families are still to be built.

## Records

Each record carries `run_id` (`model__treatment__task`), `status`, `success`,
`score`, verifier `details`, wall-clock `latency_seconds`, and the run's own
`usage` delta (requests, tokens, actual cost). `summary.json` aggregates per
treatment success rate and mean score plus total usage.

## Status

- I-family: implemented (`families/i_family.py`) as three joined tables
  (orders, customers, refunds) with an authoritative-refunds contradiction rule,
  a date window, and six checked fields.
- E-family: implemented (`families/e_family.py`) as a three-defect orders
  repository scored by verifier-owned hidden tests (buggy 0/4, reference fix
  4/4 on all five instances).
- T-family: implemented (`families/t_family.py`) as a three-episode triage
  series. Episode 1 states the rule and asks for a durable harness note (not
  scored); episode 2 withholds the rule (transfer); episode 3 changes the
  critical threshold (stale-rule detection). M-on cells share one agent
  directory across the series, M-off cells get a fresh one per episode, and
  every record reports `harness_state_bytes` so a silent no-op is visible.
- The research design sizes each family at 30 instances for the main experiment.

## Pilot result (2026-10-31)

120 cells (3 families x 5 instances x 8 treatments), cheap model, ¥26.13 total,
20.0M prompt tokens. 119 completed, one E-family timeout.

| family | success | mean score |
| --- | ---: | ---: |
| I (multi-table) | 40/40 | 1.00 |
| E (six defects) | 39/40 | 0.98 |
| T (transfer) | 27/40 | 0.89 |

The T-family carries the treatment signal: every M-on cell wrote harness state
(2.6-3.1 KB) and scored 1.0 except one 4/5, while M-off cells scored 1/5, 2/5,
2/5 and 3/5 (19/20 vs 8/20, Fisher p ~ 0.0004). I is at ceiling; E is near it.
The full-product treatment was both the most accurate (15/15) and the cheapest
(¥1.87 for its 15 cells).

`frozen-pilot.json` pins the protocol for the main run: config hash, runner and
family-module hashes, one digest per materialized instance. Note the pilot ran
with the earlier 240 s task timeout; the frozen config uses 360 s.
