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
