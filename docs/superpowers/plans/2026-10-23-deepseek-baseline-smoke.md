# DeepSeek Baseline Smoke Experiment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run three isolated, automatically verified Prime Agent tasks against `deepseek-v4-flash` while keeping conservative total cost below RMB 10 and never persisting the API key.

**Architecture:** A standard-library Python harness materializes deterministic fixtures, starts a loopback-only forwarding proxy with a six-request gate, launches the repository binary in isolated directories, verifies outputs, and writes sanitized summaries. A faux-provider rehearsal exercises the CLI path before any live request.

**Tech Stack:** Python 3 standard library, `unittest`, Rust `prime-agent` CLI, DeepSeek's OpenAI-compatible API, macOS Keychain.

---

## File map

| File | Responsibility |
| --- | --- |
| `experiments/harness-ablation/smoke/manifest.json` | Frozen model, price, FX, request, token, timeout, and task settings |
| `experiments/harness-ablation/smoke/tasks.py` | Deterministic fixtures and task-specific verification |
| `experiments/harness-ablation/smoke/budget_proxy.py` | Loopback forwarding proxy, preflight budget gate, sanitized usage ledger |
| `experiments/harness-ablation/smoke/run_smoke.py` | Isolated Prime Agent orchestration and result aggregation |
| `experiments/harness-ablation/smoke/test_smoke.py` | Offline tests for fixtures, gates, parsing, and redaction |
| `experiments/harness-ablation/smoke/README.md` | Reproduction and safety instructions |
| `experiments/harness-ablation/smoke/results/2026-10-23-summary.json` | Sanitized live result |
| `experiments/harness-ablation/smoke/results/2026-10-23-report.md` | Human-readable findings and limitations |

### Task 1: Add deterministic tasks and verifiers

**Files:**
- Create: `experiments/harness-ablation/smoke/manifest.json`
- Create: `experiments/harness-ablation/smoke/tasks.py`
- Create: `experiments/harness-ablation/smoke/test_smoke.py`

- [ ] **Step 1: Write failing fixture tests**

Define `TaskCase`, `materialize_tasks(root: Path) -> list[TaskCase]`, `fixture_digest(root) -> str`, and `verify(case, stdout) -> dict` as the required interface. Add:

```python
class TaskFixtureTests(unittest.TestCase):
    def test_materialization_is_deterministic(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            left = tasks.materialize_tasks(Path(first))
            tasks.materialize_tasks(Path(second))
            self.assertEqual([case.task_id for case in left], [
                "t1-structured", "t2-log-correlation", "t3-parser-fix"])
            self.assertEqual(tasks.fixture_digest(Path(first)),
                             tasks.fixture_digest(Path(second)))

    def test_reference_answers_pass_and_malformed_answers_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            for case in tasks.materialize_tasks(Path(directory)):
                self.assertEqual(tasks.verify(case, case.reference_output)["score"], 1.0)
                self.assertFalse(tasks.verify(case, "not valid output")["success"])
```

- [ ] **Step 2: Run tests and verify red**

```bash
python3 -m unittest experiments/harness-ablation/smoke/test_smoke.py -v
```

Expected: import failure because `tasks.py` does not exist.

- [ ] **Step 3: Implement fixtures and verifiers**

Use fixed literals and deterministic generation. T1 returns four JSON scalar fields. T2 writes about 10 KB of newline-delimited logs and verifies three linked facts. T3 writes `parser.py`, public `test_parser.py`, and verifier-owned `hidden_test_parser.py`; only the public test command appears in the model prompt. Parse the final JSON object from fenced or plain text, and run T3 hidden tests with a 20-second timeout.

Create this exact manifest:

```json
{
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "reasoning": "off",
  "input_usd_per_million": 0.15,
  "output_usd_per_million": 0.60,
  "context_tokens": 1000000,
  "response_tokens": 1024,
  "max_provider_requests": 6,
  "usd_to_cny": 8.0,
  "hard_budget_cny": 10.0,
  "task_timeout_seconds": 180
}
```

- [ ] **Step 4: Run tests and verify green**

Run Step 2 again. Expected: all fixture/verifier tests pass; T3's initial hidden failure appears only as asserted verifier data.

- [ ] **Step 5: Commit**

```bash
git add experiments/harness-ablation/smoke/manifest.json experiments/harness-ablation/smoke/tasks.py experiments/harness-ablation/smoke/test_smoke.py
git commit -m "test(experiments): add DeepSeek smoke fixtures"
```

### Task 2: Add the bounded loopback proxy

**Files:**
- Create: `experiments/harness-ablation/smoke/budget_proxy.py`
- Modify: `experiments/harness-ablation/smoke/test_smoke.py`

- [ ] **Step 1: Add failing gate tests**

Require `BudgetPolicy.from_manifest`, `UsageLedger.admit`, and `redact_headers`:

```python
class BudgetPolicyTests(unittest.TestCase):
    def test_six_full_context_requests_fit_and_seventh_is_refused(self):
        policy = budget_proxy.BudgetPolicy.from_manifest(MANIFEST)
        ledger = budget_proxy.UsageLedger(policy)
        self.assertLess(policy.worst_case_request_cny() * 6, 10.0)
        for _ in range(6):
            ledger.admit({"max_tokens": 1024})
        with self.assertRaises(budget_proxy.BudgetExceeded):
            ledger.admit({"max_tokens": 1024})

    def test_authorization_is_removed(self):
        clean = budget_proxy.redact_headers({"Authorization": "Bearer secret", "X-Test": "ok"})
        self.assertEqual(clean, {"X-Test": "ok"})
```

- [ ] **Step 2: Run tests and verify red**

Expected: `budget_proxy` import failure.

- [ ] **Step 3: Implement policy, ledger, and proxy**

Bind `ThreadingHTTPServer` to `127.0.0.1`. Forward only `POST /chat/completions` to `https://api.deepseek.com/chat/completions`. Parse JSON before forwarding, reject `max_tokens > 1024`, and admit the request before opening upstream. Stream bytes back while parsing only final SSE `usage`. Never log headers or bodies. Expose `start_proxy(policy, upstream=None) -> ProxyHandle` with `base_url`, `ledger`, and `close()`.

`UsageLedger.summary()` returns only request/token totals, computed USD/CNY cost, rejection count, and sanitized error categories.

- [ ] **Step 4: Test against a local fake upstream**

Inject a local upstream in tests. Assert a streamed response passes byte-for-byte, usage is captured, request seven gets HTTP 402 without reaching upstream, and an excessive token limit gets HTTP 400.

- [ ] **Step 5: Run and commit**

```bash
python3 -m unittest experiments/harness-ablation/smoke/test_smoke.py -v
git add experiments/harness-ablation/smoke/budget_proxy.py experiments/harness-ablation/smoke/test_smoke.py
git commit -m "feat(experiments): add bounded DeepSeek proxy"
```

### Task 3: Add isolated orchestration

**Files:**
- Create: `experiments/harness-ablation/smoke/run_smoke.py`
- Modify: `experiments/harness-ablation/smoke/test_smoke.py`

- [ ] **Step 1: Add failing command/environment tests**

```python
class RunnerTests(unittest.TestCase):
    def test_command_and_environment_are_isolated(self):
        command = run_smoke.build_command(Path("/bin/prime-agent"), "prompt")
        self.assertIn("json", command)
        self.assertNotIn("secret", " ".join(command))
        with tempfile.TemporaryDirectory() as directory:
            env = run_smoke.isolated_env(Path(directory), "secret")
            self.assertEqual(env["DEEPSEEK_API_KEY"], "secret")
            self.assertEqual(env["PI_OFFLINE"], "1")
            self.assertTrue(Path(env["PRIME_AGENT_CODING_AGENT_DIR"]).is_absolute())
```

- [ ] **Step 2: Run tests and verify red**

Expected: `run_smoke` import failure.

- [ ] **Step 3: Implement `--dry-run`, `--faux`, and `--live`**

Read the key with `security find-generic-password -a "$USER" -s prime-agent-deepseek-smoke -w` using captured stdout; strip it and never include it in exceptions. Generate isolated `models.json` overriding `deepseek-v4-flash` with the proxy base URL and `maxTokens: 1024`. Run tasks sequentially with `subprocess.run(capture_output=True, timeout=180)`. Stop on missing usage, proxy rejection, timeout, authentication/balance/rate-limit error, or failed preflight.

`--dry-run` materializes/verifies fixtures only. `--faux` sets `PRIME_AGENT_FAUX_SCRIPT` and never reads Keychain. `--live` requires Keychain and proxy.

- [ ] **Step 4: Test failure policies and secret rejection**

Use synthetic executables to return JSON, time out, emit a secret-shaped string, and fail. Assert timeout stops later tasks, nonzero exits are classified without raw stderr, and serialization refuses any `sk-` string with at least 20 key characters.

- [ ] **Step 5: Run and commit**

```bash
python3 -m unittest experiments/harness-ablation/smoke/test_smoke.py -v
git add experiments/harness-ablation/smoke/run_smoke.py experiments/harness-ablation/smoke/test_smoke.py
git commit -m "feat(experiments): orchestrate isolated smoke runs"
```

### Task 4: Rehearse offline

**Files:**
- Create: `experiments/harness-ablation/smoke/README.md`
- Modify only if rehearsal finds a defect: Task 1–3 files

- [ ] **Step 1: Document secure Keychain setup/removal**

```bash
security add-generic-password -U -a "$USER" -s prime-agent-deepseek-smoke -w
security delete-generic-password -a "$USER" -s prime-agent-deepseek-smoke
```

State that `-w <value>` must never be placed in shell history.

- [ ] **Step 2: Build the binary**

```bash
cargo build -p pa-cli --bin prime-agent
```

Expected: exit 0 and `target/debug/prime-agent` exists.

- [ ] **Step 3: Run dry and faux rehearsals**

```bash
python3 experiments/harness-ablation/smoke/run_smoke.py --dry-run --binary target/debug/prime-agent
python3 experiments/harness-ablation/smoke/run_smoke.py --faux --binary target/debug/prime-agent
```

Expected: no network or Keychain access, three result records, zero provider requests/cost.

- [ ] **Step 4: Run safety checks**

```bash
python3 -m unittest experiments/harness-ablation/smoke/test_smoke.py -v
git diff --check
rg -n 'sk-[A-Za-z0-9_-]{20,}' experiments/harness-ablation/smoke
```

Expected: tests pass, diff check exits 0, secret scan has no matches.

- [ ] **Step 5: Commit**

```bash
git add experiments/harness-ablation/smoke/README.md experiments/harness-ablation/smoke
git commit -m "docs(experiments): document DeepSeek smoke run"
```

### Task 5: Run live and publish sanitized evidence

**Files:**
- Create: `experiments/harness-ablation/smoke/results/2026-10-23-summary.json`
- Create: `experiments/harness-ablation/smoke/results/2026-10-23-report.md`

- [ ] **Step 1: Confirm Keychain presence without displaying the key**

Check only the exit status of `security find-generic-password`; do not use `-w` for this check and do not print metadata.

- [ ] **Step 2: Run live once**

```bash
python3 experiments/harness-ablation/smoke/run_smoke.py --live --binary target/debug/prime-agent
```

Expected: no more than three tasks or six provider calls, sanitized result files, and conservative total below RMB 10. Do not retry authentication, balance, rate-limit, usage, or budget failures.

- [ ] **Step 3: Verify evidence**

```bash
python3 -m json.tool experiments/harness-ablation/smoke/results/2026-10-23-summary.json
python3 -m unittest experiments/harness-ablation/smoke/test_smoke.py -v
rg -n 'sk-[A-Za-z0-9_-]{20,}' experiments/harness-ablation/smoke
git diff --check
```

Expected: valid JSON, passing tests, no secret match, clean diff check.

- [ ] **Step 4: Review report scope**

Confirm exact task count, scores, usage, actual/conservative cost, errors, model/commit, and the limitation that no `R/S/M` treatment varied.

- [ ] **Step 5: Commit and push**

```bash
git add experiments/harness-ablation/smoke/results/2026-10-23-summary.json experiments/harness-ablation/smoke/results/2026-10-23-report.md
git commit -m "results: record DeepSeek baseline smoke experiment"
git push fork research/harness-ablation
```

Never commit raw streams, sessions, temporary workspaces, Keychain output, or environment dumps.
