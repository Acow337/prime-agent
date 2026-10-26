# DeepSeek baseline smoke experiment

This directory contains a bounded, automatically verified baseline smoke test
for `deepseek/deepseek-v4-flash`. It runs three deterministic tasks through the
real Prime Agent headless path. This is not an R/S/M ablation: no harness
treatment varies, so the result supports only a single-model baseline check.

## Safety envelope

- The forwarding proxy binds only to `127.0.0.1` and forwards only
  `POST /chat/completions`.
- At most six provider requests are admitted, each with `max_tokens <= 1024`.
- The conservative gate assumes the full 1,000,000-token context for every
  admitted request and rejects projected spend at or above the ¥10 limit.
- Authentication, balance, rate-limit, missing-usage, proxy, and timeout
  failures stop the run without retrying.
- Headers, prompts, response bodies, raw stderr, sessions, temporary
  workspaces, and credentials are never written to result artifacts.

The manifest pins the experiment prices and an intentionally conservative
USD/CNY conversion rate. Check those values before treating the reported cost
as a current provider quote.

## Store the credential securely

Add or update the key in macOS Keychain. Keep `-w` as the final option so the
`security` command prompts interactively:

```bash
security add-generic-password -U -a "$USER" -s prime-agent-deepseek-smoke -w
```

Never write `-w <value>` in a command: that exposes the credential through
shell history and process arguments. Confirm presence without displaying the
value:

```bash
security find-generic-password -a "$USER" -s prime-agent-deepseek-smoke >/dev/null
```

Remove the entry after the experiment:

```bash
security delete-generic-password -a "$USER" -s prime-agent-deepseek-smoke
```

If a key was pasted into a chat or another non-secret channel, rotate it after
the experiment even if it was never committed to the repository.

## Rehearse offline

Build the binary, then run both offline lanes:

```bash
cargo build -p pa-cli --bin prime-agent
target/debug/prime-agent --prime-agent-bootstrap
python3 experiments/harness-ablation/smoke/run_smoke.py --dry-run --binary target/debug/prime-agent
python3 experiments/harness-ablation/smoke/run_smoke.py --faux --binary target/debug/prime-agent
```

The bootstrap is a one-time setup for the isolated Python kernel used by the
code-editing task. It may download Python packages, but it does not contact the
model provider or incur API cost. Once bootstrap finishes, both rehearsal lanes
run offline.

`--dry-run` verifies the fixtures and reference answers without launching the
binary. `--faux` exercises the real CLI, event parsing, Python tool, file
mutation, and hidden verifier with scripted local responses. Neither lane reads
Keychain or makes a provider request; both must report zero usage and cost.

## Run live once

After the offline lanes pass and the Keychain item exists:

```bash
python3 experiments/harness-ablation/smoke/run_smoke.py --live --binary target/debug/prime-agent
```

Do not automatically rerun a failed live command. Inspect the sanitized status
first. The command writes only these reviewable artifacts:

- `results/2026-10-23-summary.json`
- `results/2026-10-23-report.md`

Before committing results, run:

```bash
python3 -m json.tool experiments/harness-ablation/smoke/results/2026-10-23-summary.json
python3 -m unittest experiments/harness-ablation/smoke/test_smoke.py -v
rg -n 'sk-[A-Za-z0-9_-]{20,}' experiments/harness-ablation/smoke
git diff --check
```

The secret scan should produce no matches. Never commit raw CLI streams,
sessions, Keychain output, environment dumps, or temporary workspaces.
