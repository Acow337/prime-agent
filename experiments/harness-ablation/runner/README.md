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

Live mode reads the credential for the config's `provider` from macOS Keychain
(services per provider: `KEYCHAIN_SERVICES` in `../smoke/run_smoke.py`; DeepSeek
is `prime-agent-deepseek-smoke`) and hands it only to the bounded loopback proxy
from `../smoke`, which authenticates every upstream request and forwards only to
that provider's own endpoint (`UPSTREAMS` in `../smoke/budget_proxy.py`); the
agent sees only the placeholder `proxy-managed`. It sets `PRIME_AGENT_HARNESS_TREATMENT` for each
cell and writes only `records.jsonl` + `summary.json` under `--results`.

## OpenRouter runs

A config with `"provider": "openrouter"` (Keychain service `prime-agent-openrouter`)
names OpenRouter model ids under `models` and must pin, in `request_fields`, what
every request is sent with. The proxy sets those fields on each request before
admission, replacing the agent's own values, and refuses to start an OpenRouter
run without exactly one pinned provider endpoint, fallbacks off, and an explicit
reasoning mode:

```json
"provider": "openrouter",
"models": {"cheap": "deepseek/deepseek-v4-flash"},
"request_fields": {
  "provider": {"order": ["deepinfra/fp8"], "allow_fallbacks": false},
  "reasoning": {"effort": "none"}
}
```

- Endpoint slugs (`provider[/variant][/region]`) and each model's reasoning
  options come from `GET https://openrouter.ai/api/v1/models/{id}/endpoints` and
  `GET https://openrouter.ai/api/v1/models`. Omitting `reasoning` would leave
  each model's own default in force (for example, the listing gives
  `deepseek/deepseek-v4-flash` a default effort of `high`).
- The agent sends `max_completion_tokens`, while most endpoints list only
  `max_tokens` among their supported parameters; check this with a one-cell
  run before adding `"require_parameters": true` to the pin.
- `input_usd_per_million` and `output_usd_per_million` must be at least the
  pinned endpoint's highest listed rates, time-of-day overrides included
  (DeepSeek's own endpoint doubles them in weekday peak hours). Billed cost
  therefore varies with the hour; compare costs across arms from token counts
  at fixed rates. `freeze.py` snapshots that endpoint's listing
  (name with model version, quantization, context and output limits, pricing,
  supported parameters) into the frozen file as `prices` and refuses a config
  priced below it, since the budget gate prices the next request's worst case at
  the config's rates.
- The top-level `"reasoning": "off"` in the DeepSeek configs is not sent
  anywhere: the agent's models config disables pa-ai's reasoning controls, so
  those runs used the provider's default. Only `request_fields` reach the wire.

## Matrix

`config.pilot.json` enables the cheap model and the I-family instances 1–5, so a
dry run is 5 × 8 = 40 cells. Adding the E and T families (`families/e`,
`families/t`) to the same config reaches the designed pilot of 3 × 5 × 8 = 120
runs; those families are still to be built.

## Records

Each record carries `run_id` (`model__treatment__task`), `status`, `success`,
`score`, verifier `details`, wall-clock `latency_seconds`, and the run's own
`usage` delta (requests, tokens, actual cost). The delta also carries
`reported_cost_usd`, the part of the cost the provider billed and reported
(OpenRouter does; DeepSeek's direct API does not, so its cost is priced from
tokens), and `served_by`, a count of responses per model and serving provider as
the stream named them (`"deepseek/deepseek-v4-flash-20260423 @ DeepInfra": 3`;
a direct provider names only the model). A cell that hit proxy errors also
carries them as `cell_error_categories`: a quota rejection alone makes the
status `budget_exhausted` (an agent failure, counted and not retried); any other
proxy error makes it `infra_error` (retried). Records written before this
classification store the invocation's cumulative `error_categories` and mark
budget exhaustion as `infra_error`; `counted_records` recognizes those by their
request count reaching the quota.

A cell's counted attempt is its first `completed` or `budget_exhausted` one
under the run's final `max_requests_per_run`; analysis scripts take the run's
config for that reason (`analyze.py --config ... --results ...`). `summary.json`
aggregates per treatment success rate and mean score over the counted cells,
plus total usage (including billed cost and `served_by`) over every record in
`records.jsonl`, including cells written by earlier invocations of a resumed run.

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
