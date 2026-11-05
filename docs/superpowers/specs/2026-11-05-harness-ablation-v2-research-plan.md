# Harness Ablation v2: Research Plan

Follow-up to `2026-10-18-harness-ablation-research-design.md`. Written 2026-11-05,
after the v1 draft (`paper/paper.md`) and its validity audit.

## 1. Where v1 stands

### 1.1 What v1 established

- **Cost multipliers are real and robust.** The fully enabled harness is the cheapest arm
  (¥16.87 vs ¥32.27 for all-off per 90 cells); turning R off raises requests by 13–41% by
  family; turning M off roughly doubles transfer spend.
- **The treatment layer and measurement harness work.** R/S/M switches sit at admission
  boundaries, are verified at the model-visible surface, and leave the product path unchanged;
  the runner meters every cell through a budget proxy.
- **A conditional positive result.** When the needed state is non-derivable and must persist,
  M converts into accuracy (hidden-policy probe: 0.936 vs 0.455, 6 vs 4 cells).

### 1.2 What v1 did not establish

- **The accuracy null is mostly a ceiling effect.** Every arm solves 83–88 of 90 cells; the
  I family is 29/30 in every arm. The families meant to stress R, S, and M do not require them.
- **The M null is threatened by leakage.** In the main matrix and the mechanism check, M-off
  episodes could reach sibling episode directories through relative paths, and 603 of 720 main
  cells were recorded while a series also shared HOME and TMPDIR.
- **The positive results are thin.** The hidden-policy probe is close to a positive control
  (the state exists only in memory by construction), and the strong-model replication has 10
  cells per arm contrasting full vs all-off, so it does not isolate M.
- **S is effectively untested.** No task in the suite needs delegation.
- **External benchmarks resolve only large effects** (n ≈ 20, minimum detectable effect ≈ 30
  points), and two of them — LongBench-v2 and LongMemEval, plus the boundary check — changed
  request quotas mid-run.
- **No transcripts were kept.** The runner executes each run under a temporary directory, so
  session transcripts are deleted after the run. Failure analysis (including the strong-model
  anomaly: all-off scores 0.710 with `deepseek-v4-pro` but 0.990 with `deepseek-v4-flash`) and
  leak audits are impossible for v1 data.

### 1.3 The lead that reshapes v2

The main matrix capped cells at 40 requests and only one cell hit the cap, so every counted
cell's trajectory is effectively uncapped. That makes **budget-constrained accuracy** computable
from existing records: `success@q` is the share of counted cells that succeeded with
`usage.request_count <= q` (cells from `results/main-720/records.jsonl`, counted with
`runner.counted_records`). Exploratory values:

| Family · contrast | q | On | Off | Gap |
| --- | ---: | ---: | ---: | ---: |
| T · M (all R/S arms, n = 120 each) | 15 | 0.34 | 0.01 | +33 |
| | 20 | 0.62 | 0.10 | +52 |
| | 25 | 0.83 | 0.53 | +30 |
| | 40 (actual quota) | 0.96 | 0.97 | −1 |
| T · full vs M-off only (n = 30 each) | 20 | 0.77 | 0.13 | +64 |
| I · R (all S/M arms, n = 120 each) | 4 | 0.72 | 0.26 | +46 |
| | 6 | 0.95 | 0.63 | +32 |
| | 8 | 0.97 | 0.95 | +2 |
| E · full vs R-off only (n = 30 each) | 10 | 0.50 | 0.07 | +43 |
| | 12 | 0.77 | 0.20 | +57 |
| | 15 | 0.87 | 0.53 | +34 |
| E · S (all R/M arms, n = 120 each) | 12 | 0.55 | 0.41 | +14 |
| | 20 | 0.82 | 0.88 | −6 |

Median requests per cell: T 18 (M-on) vs 25 (M-off); I 4 (R-on) vs 6 (R-off); E 10 (full)
vs 14 (R-off).

Reading: R and M are efficiency. Under v1's generous quota the efficiency surfaces only as cost;
under a binding budget it becomes a large accuracy difference. "Cost doubles" and "accuracy is
flat" are two views of one phenomenon. S shows no reliable effect on the current tasks.

Caveats that make this a lead, not a result:

- It is **post hoc**: the hypothesis was formed after seeing the data, so it must be confirmed
  on fresh, pre-registered runs (§6).
- `success@q` assumes a cut run fails. A real capped run can still emit a partially correct
  answer, and the bias grows with the requests an arm uses, so it may **overstate** the gaps.
- The T M-off curve inherits the v1 isolation leak; leakage would make M-off look *more*
  efficient, so the true gap could be larger.

## 2. Thesis for v2

> Harness capabilities are efficiency multipliers. Under a generous budget they show up only as
> cost; once the budget binds they become accuracy. Durable state is the exception: when the
> needed state cannot be re-derived from the inputs, it pays at any budget.

The study is organized around two dials and one generalization axis:

- **Budget slack:** requests, cost, and wall-clock time available per task.
- **State derivability:** how much of the needed state can be re-derived from the task's
  inputs, and how long it must persist.
- **Model:** whether the curves and their crossover points move across models and providers.

## 3. Research questions

- **RQ1 · Budget.** How does each capability's accuracy value change with the budget (requests,
  cost, time)? At what budget does each gap close?
- **RQ2 · Derivability.** How does the value of M depend on the derivable fraction of the needed
  state and on how many episodes it must survive?
- **RQ3 · Delegation.** On which tasks does S pay, and in which currency — accuracy, cost, or
  latency?
- **RQ4 · Generality.** Do the curves replicate on another model and provider? What explains the
  strong-model anomaly?

## 4. Hypotheses (proposed; thresholds are fixed in the pre-registration before Phase 2)

The exploratory gaps in §1.3 are optimistic by construction, so the proposed thresholds are set
well below them.

- **H1 · Budget.** On fresh capped runs:
  - T: at a 20-request quota, M-on minus M-off success is at least 20 points.
  - I: at a 6-request quota, R-on minus R-off success is at least 15 points.
  - E: at a 12-request quota, full minus R-off success is at least 20 points.
  - At the generous quota (40), each of these gaps is within ±5 points.
- **H2 · Derivability.** The M effect decreases monotonically with the derivable fraction of the
  rule: at least 30 points when nothing is derivable, within ±5 points when everything is
  (at a generous budget).
- **H3 · Delegation.** On parallelizable large-input tasks, S-on reduces wall-clock time by at
  least 25% or raises success at a binding budget; on small multi-defect fixes (E) it shows no
  reliable effect.
- **H4 · Generality.** The direction of H1 replicates on a second provider; crossover budgets may
  shift with the model.

## 5. Infrastructure changes (Phase 0, before any new runs)

1. **Land the v1 audit.** Commit the audit fixes (runner accounting, regenerated summaries and
   reports, paper corrections) and the progress deck.
2. **Paper numbers from code.** One script derives every number and table the paper quotes from
   the result files; the paper consumes its output. No hand-copied numbers.
3. **Budget-curve analysis.** Commit a `budget_curves.py` beside the runner: `success@q` by
   requests, cost, and wall-clock time, per family × treatment, with instance-paired bootstrap
   intervals.
4. **Paired factorial analysis.** Every instance runs under all arms, so estimate factor effects on
   instance-blocked contrasts (paired bootstrap), and fit log requests / log cost ~ R×S×M with
   instance as a random effect if a mixed-model dependency (for example statsmodels) is accepted.
   Report cost multipliers with intervals; v1 reported cost totals without them.
5. **Runner hardening.**
   - Keep transcripts: compress each cell's session files into the results directory.
   - Freeze the protocol: the first invocation writes the config hash into the results
     directory, and a resume with a different hash fails unless an explicit, recorded override is
     given. This prevents mid-run quota changes.
   - Record the quota and timeout in force on every attempt.
   - Add a leak audit: scan transcripts for file reads outside the episode's own working tree.
6. **Pre-registration.** A short document fixing hypotheses, metrics, sample sizes, analysis, and
   stopping rules, hashed into the frozen protocol before Phase 2 starts.

## 6. Phased plan

Cost estimates are rough, scaled from v1 per-cell costs (`deepseek-v4-flash` unless noted), and
include a small allowance for transport retries.

### 6.1 Phase 0 · this week · no provider spend

The infrastructure in §5, plus the full budget-curve and factorial re-analysis of v1 data. The
re-analysis is exploratory and is labeled as such in the paper.

### 6.2 Phase 1 · weeks 1–2 · ≈ ¥250 · close the validity gaps

| Run | Design | Estimate |
| --- | --- | ---: |
| Transfer family re-run | All eight arms × 30 instances (240 cells) under full isolation and the frozen protocol. All arms re-run together, so pairing and service conditions stay matched. | ≈ ¥130 |
| Mechanism check re-run | 40 cells under full isolation | ≈ ¥20 |
| Fixed-quota external re-runs | LongBench-v2 (30 requests), LongMemEval (160), and the boundary check (80), every cell under one quota | ≈ ¥75–110 |

**Decision point D1:** if T M-off accuracy at the 40-request quota falls by 5 or more points
relative to v1, the v1 M null was partly leakage; report it and reframe the transfer result.

### 6.3 Phase 2 · weeks 2–4 · ≈ ¥700–850 · the two dials

**Experiment A · budget gradient (confirmatory, tests H1).**

- Families and quotas, chosen where the exploratory curves separate most: T at 15/20/25, I at
  4/6/8, E at 10/12/15, plus the generous 40 as the reference already covered by Phase 1 and v1.
- Arms: full, R-off, S-off, M-off, all-off; 30 instances per family, every instance under every
  arm and quota.
- Metric: success under the real cap (primary), mean score (secondary).
- Validation of the counterfactual: compare real capped success with `success@q` from uncapped
  runs per arm and quota. If they agree (absolute difference within 5 points, or the paired
  interval of the difference covers zero), "one uncapped run yields the whole budget curve"
  becomes a methods contribution.
- Optional variant: state the budget in the prompt and test whether a budget-aware agent closes
  the gap by planning differently.
- Estimate: ≈ ¥250–300 (capped cells are cheap).

**Experiment B · derivability gradient (tests H2).**

- A generator in the style of the hidden-policy probe, where a controlled fraction of the
  code→queue mapping is inferable from the inputs: 0, 25, 50, 75, or 100%.
- Six-episode series (one statement episode, five scored), 20 series per level per arm; M-on vs
  M-off with R and S on.
- Optional persistence add-on at 0% derivable: 3, 6, and 12 episodes, 15 series per arm
  (≈ ¥100 more).
- Estimate: ≈ ¥250–300 for the core design.

**Experiment C · natural non-derivable state (ecological validity for RQ2).**

- 20–30 multi-session coding scenarios. Session 1 contains a decision or preference that never
  reaches the repository (a code-style rule, a target API version, a team convention, where a
  config lives); later sessions need it.
- M-on vs M-off; the verifier checks that later sessions honor the decision.
- Estimate: ≈ ¥100–150.

**Experiment D · delegation (tests H3).**

- Parallelizable large-input tasks: multi-document synthesis and many-file search or migration.
  v1 hints at this: under LongBench-v2's original 8-request quota every arm without S ran out of
  budget in 4–5 of 20 cells, and the full harness in none.
- Arms: full vs S-off (plus all-off as a reference); record wall-clock time as a first-class
  outcome.
- Estimate: ≈ ¥100.

**Decision points.** D2: if real capped success departs from the counterfactual beyond the
agreement criterion, report real capped curves only and drop the methods claim. D3: if B shows
no monotone gradient, report the shape found (for example a threshold) and revisit how
derivability is operationalized.

### 6.4 Phase 3 · weeks 4–6 · ≈ ¥550–850 · generality

- **Second provider:** Experiment A on a subset (one quota per family below the crossover plus
  the reference), three levels of Experiment B, and Experiment C. The estimate depends on that
  provider's pricing (≈ ¥300–600).
- **`deepseek-v4-pro` on all three families:** the Experiment A subset, with transcripts, to
  explain why the strong model fails to reconstruct withheld conventions (≈ ¥250).
- **Decision point D4:** if the second provider shows no separation, scope the claims to the
  models tested.

### 6.5 Phase 4 · weeks 6–8 · writing

- Restructure the paper around the two dials. Core figures:
  - success-vs-budget curves per treatment, by requests, cost, and time;
  - the M effect vs derivability (and persistence);
  - factor cost multipliers with intervals;
  - cross-model comparison.
- An evaluation-pitfalls section: budget exhaustion misclassified as infrastructure and retried;
  selection effects from mid-run quota changes; isolation leaks; the counterfactual budget
  method and its validation.
- Related work: position against scaffold and interface ablations (for example SWE-agent),
  simpler-pipeline baselines (for example Agentless), and memory and skill-library agents (for
  example MemGPT, Reflexion, Voyager).
- Pick a target venue and schedule backwards from its deadline.

## 7. Budget and timeline

| Phase | Weeks | Provider spend (rough) |
| --- | --- | ---: |
| 0 · infrastructure and re-analysis | this week | 0 |
| 1 · validity re-runs | 1–2 | ≈ ¥250 |
| 2 · Experiments A–D | 2–4 | ≈ ¥700–850 |
| 3 · generality | 4–6 | ≈ ¥550–850 |
| 4 · writing | 6–8 | 0 |
| **Total** | ≈ 8 weeks | **≈ ¥1,500–1,950** (v1 spent ≈ ¥465) |

## 8. Risks and fallbacks

| Risk | Mitigation or fallback |
| --- | --- |
| HARKing: the budget hypothesis came from v1 data | Pre-register H1–H4 and the analysis before Phase 2; label the v1 re-analysis exploratory |
| Provider instability (v1's main run needed 447 infrastructure retries) | Frozen protocol, per-run hard budget caps, transport retries owned by the proxy |
| Experiment A does not reproduce the counterfactual curves | Report real capped curves; fall back to the cost-multiplier paper, which v1 already supports |
| Experiment B's generator leaks the rule through other channels | Leak audit on transcripts; an M-off ceiling at 0% derivable flags it |
| Scope creep | Hold to the minimum viable version below |
| Cost overrun | Each phase has its own hard cap in the runner config; stop and re-plan at a cap |

## 9. Minimum viable version

If time is short: Phase 0 + Phase 1 + Experiment A, with a reduced Experiment B (three
derivability levels). That alone turns v1's "no accuracy effect, with one exception" into a
budget-dependent effect with a validated measurement method — a much stronger paper.

## 10. Deliverables

- Pre-registration document, frozen with the protocol hashes.
- Hardened runner (transcripts, protocol freeze guard, per-attempt quota and timeout, leak audit)
  with tests.
- Analysis scripts: budget curves, paired factorial analysis, paper-number generation.
- Results for Phases 1–3 with transcripts, summaries, and analysis reports.
- Paper v2 built around the two dials, with the evaluation-pitfalls section.

## 11. Responsibility and disclosure

- Exploratory analyses of v1 data are labeled exploratory; confirmatory claims rest on
  pre-registered runs.
- Every protocol change after a run starts is recorded with its reason, and the affected runs are
  re-run in full rather than patched.
- The v1 audit disclosures (failure accounting, mid-run quota changes, the runner change during
  the main run, isolation limits) carry into v2 and stay in the paper.
