# Harness Ablation v2: Research Plan

Follow-up to `2026-10-18-harness-ablation-research-design.md`. Written 2026-11-05 after the v1
draft (`paper/paper.md`) and its validity audit; revised 2026-11-06 after a treatment-validity
check (§1.2) and a review of the factor definitions (§3).

## 1. Where v1 stands

### 1.1 What v1 established

- **Cost multipliers are real and robust.** The fully enabled harness is the cheapest arm
  (¥16.87 vs ¥32.27 for all-off per 90 cells); turning R off raises requests by 13–41% by
  family; turning M off roughly doubles transfer spend. Every leak found later (§1.2) makes the
  ablated arms look better, so these gaps are conservative.
- **The switches work as implemented.**
  - R: the namespace reset fires at every model-turn end (`AgentEvent::TurnEnd`), and turning R
    off raises requests in every family.
  - M: the write gate holds on the harness API. 0 of 120 main-matrix M-off transfer cells wrote
    harness state, against 90 of 120 M-on cells.
  - S: print mode (`HeadlessEmbedding::Root`) wires an inline child host that S-off withholds, so
    the capability really differs between arms.
- **Measurement is reliable.** A 60-cell repeat of the full-vs-off contrast reproduced 55 of 60
  scores exactly.

### 1.2 What v1 did not establish

- **The accuracy null is mostly a ceiling effect.** Every arm solves 83–88 of 90 cells; the I
  family is 29/30 in every arm. The families meant to stress R, S, and M do not require them.
- **The M null is probably a leakage artifact.**
  - Transfer episodes 2–3 hand over unlabeled rows. Neither the rule's structure nor the full
    label vocabulary can be derived from them: labeling every row with the one label pair shown
    in the format example scores 0.54, yet M-off solved 116 of 120 cells.
  - The runner did not pass `--no-session`, so every episode's transcript, rule text included,
    was written to its session directory. In the main matrix and the mechanism check, the
    previous episode's session directory sat beside the current one.
  - M-off used only 2–5 more requests per scored episode than M-on (main-matrix medians 9 and 10
    vs 7 and 6): the signature of a lookup, not of re-deriving a rule.

  Transcripts were not kept, so the channel cannot be confirmed; the sandboxed re-run in §7.2
  settles it.
- **Arms could see each other's files.** The runner materialized each task under
  `tasks/<task id>`, shared by all eight arms of an invocation, and only the E family clears that
  directory. Files an earlier arm's agent wrote stayed visible to later arms in every other family
  and benchmark.
- **Isolation varied across runs, and no run used a real sandbox.**

  | Isolation of multi-episode M-off runs | Runs |
  | --- | --- |
  | Sibling episode directories reachable; 603 main cells also shared HOME and TMPDIR | main matrix, mechanism check |
  | Fresh per-episode HOME, TMPDIR, and agent directory with copied inputs; siblings still reachable | boundary check, LongMemEval, 60-cell repeat |
  | Per-episode temporary root (`mkdtemp`), placed next to the runner's own temporary tree | hidden-policy probe, both strong-model runs |

- **The hidden-policy probe measured acquisition cost, not memory.** M-off routed tickets
  perfectly in 14 episodes, which is impossible without the mapping, spending 13–27 requests per
  episode (M-on: about 5); its failures were budget exhaustion and timeouts. The policy episode
  writes the mapping to `policy.txt`, the M-on arm's ticket directories sit beside it, and M-on
  cell `hl-3-1` scored 1.0 with no harness state at all.
- **The strong-model "model dependence" is confounded with isolation.** `deepseek-v4-pro` ran
  under per-episode temporary roots and `deepseek-v4-flash`'s main matrix under the weak layout,
  so the all-off gap (0.710 vs 0.990) cannot be attributed to the model. The strong model's
  all-off arm shows the same lookup signature: its perfect episodes cost 9–31 requests, against
  3–13 for the full harness.
- **Capability uptake is unknown or partial.** No record shows whether S-on agents spawned
  children; 30 of 120 M-on transfer cells never wrote a memory; in LongMemEval 12 of 20 M-off cells
  still left a harness state file, possibly written to disk directly.
- **External benchmarks resolve only large effects** (n ≈ 20, minimum detectable effect ≈ 30
  points), and two of them — LongBench-v2 and LongMemEval, plus the boundary check — changed
  request quotas mid-run.
- **No transcripts were kept.** The runner executes each run under a temporary directory and
  deletes it afterwards, so failure analysis and leak audits are impossible for v1 data.

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

The same records give each capability a **budget multiplier**: the budget the ablated arm needs to
solve the same share of cells, relative to the enabled arm.

| Capability · family | Requests to solve 50% (on → off) | Multiplier | In cost | Largest gap |
| --- | --- | ---: | ---: | --- |
| M · T | 18 → 25 | ×1.39 | ×2.22 | 52 points at 20 requests |
| R · I | 4 → 6 | ×1.50 | ×1.65 | 51 points at 5 requests |
| R · E | 11 → 15 | ×1.36 | ×1.42 | 43 points at 11 requests |
| S · E | 12 → 14 | ×1.17 | ×1.16 | 14 points at 12 requests; none at 80% solved |

Reading: R and M are efficiency. Under v1's generous quota the efficiency surfaces only as cost;
under a binding budget it becomes a large accuracy difference. "Cost doubles" and "accuracy is
flat" are two views of one phenomenon. S shows no reliable effect on the current tasks.

Caveats that make this a lead, not a result:

- It is **post hoc**: the hypothesis was formed after seeing the data, so it must be confirmed on
  fresh, pre-registered runs (§7.3).
- `success@q` assumes a cut run fails. A real capped run can still emit a partially correct
  answer, and the bias grows with the requests an arm uses, so it may **overstate** the gaps.
- For M, M-off re-acquired the rule by lookup (§1.2), so the M multiplier is a lower bound. In a
  sandbox where the rule cannot be re-acquired, the two curves need not converge at all.

## 2. Thesis for v2

> Harness capabilities are budget multipliers: under a generous budget they show up only as
> cost; once the budget binds they become accuracy. Whether durable state is irreplaceable —
> paying at any budget when the needed state cannot be re-acquired — is open, because v1's probe
> of it leaked.

The study is organized around two dials and one generalization axis:

- **Budget slack:** requests, cost, and wall-clock time available per task.
- **Re-acquisition cost:** how expensive — or impossible — it is to get the needed state back
  without the capability, by re-deriving, re-reading, or re-searching it.
- **Model:** whether the curves and their crossover points move across models and providers,
  compared under identical isolation.

## 3. Factor redefinition

### 3.1 What the v1 factors measured

| Factor | v1 definition | What it actually measured | What it missed |
| --- | --- | --- | --- |
| R | Python values survive a settled model turn | The cost of rebuilding in-memory state every turn. Files stay on disk, so it can be worked around. | RLM's core claim — reaching context through code instead of reading it into the prompt — stays on in R-off: OOLONG shows R-off still handles 4M-token inputs within a turn. |
| S | The session may spawn recursive child sessions | Availability of full child agents | RLM's recursion is a lightweight sub-model call. Delegation's natural payoffs — parallel latency and fresh context windows — were not measured, and no task needed delegation. |
| M | Harness state is read, injected, and refined | Store-and-recall of one note across 2–3 short episodes, bundled with a shared filesystem for M-on | The continual harness's refinement (behavior improving through `/refine` over many episodes), and memory surviving compaction inside a long session. |

### 3.2 Independent switches, entangled effects

The switches are independent in code; their effects are not.

- **R × S:** R-off clears spawned job handles along with the rest of the namespace, so collecting a
  child's result in a later turn takes extra steps.
- **S × M:** reusable sub-agent specifications are part of the continual harness, so M-off also
  removes state that S can draw on.
- **M × filesystem:** M-on shared HOME, TMPDIR, and the task tree across episodes; M-off did not.

The v1 paper's "orthogonal by construction" should be narrowed to the switches. Interactions are
estimated, not assumed away.

### 3.3 v2 factor set

| Factor | On | Off | Primary outcome |
| --- | --- | --- | --- |
| **P · programmatic context** (new; RLM's core) | Task content is reachable only as files or variables through code | Content is inlined into the prompt; code cannot read the task files | Success against input size (within vs beyond one context window), cost |
| **R · namespace persistence** (kept, secondary) | As v1 | As v1 | Requests and cost: the rebuild overhead |
| **S-call · sub-model calls** (optional) | Lightweight model calls from code, added behind the experiment entry point if the runtime lacks one | Refused | Success on chunked long inputs, cost |
| **S-agent · child agents** | As v1 S | As v1 S-off | Wall-clock time, the root session's peak context tokens, success at a binding budget |
| **M-recall · durable memory** | Harness state is read and injected | Pinned empty harness | Success at a binding budget across episodes |
| **M-refine · self-improvement** | `/refine` and auto-refine enabled | Disabled, with M-recall still on | Improvement over a long episode series with verifier feedback |

Every factor must meet five requirements:

1. **One admission boundary** in code, reachable only through the experiment entry point.
2. **A manipulation check** at the model-visible surface, as in v1.
3. **An uptake metric recorded per cell:**
   - P: code executions that touch task files, and prompt size;
   - R: resets and names cleared;
   - S: sub-model calls and child spawns;
   - M: harness reads, writes, and digest size.
4. **A task family that needs the factor**, scored on the factor's natural outcome.
5. **Isolation that holds.** An off arm cannot re-acquire the capability's state through another
   channel (§6), and M-on shares only the harness directory.

### 3.4 Design consequences

- Six factors are too many for a full factorial. Main effects come from one-factor-off contrasts
  against the full harness (plus all-off as a reference); small factorials are run only where an
  interaction is hypothesized: R × S-agent (job handles) and M-recall × M-refine.
- Testing P needs inputs on both sides of the context window, for example OOLONG-style length
  bands.
- S-agent and M-refine need new task families: the v1 E family never needs delegation, and the
  T family never exercises refinement.

## 4. Research questions

- **RQ1 · Budget.** How does each capability's accuracy value change with the budget (requests,
  cost, time)? At what budget does each gap close?
- **RQ2 · Re-acquisition.** How does the value of M-recall depend on how costly the needed state
  is to re-acquire without it, up to impossible?
- **RQ3 · Programmatic context.** Does P pay in accuracy beyond one context window, and in cost
  within it?
- **RQ4 · Delegation.** Where do S-call and S-agent pay, and in which currency: accuracy, cost,
  latency, or context capacity?
- **RQ5 · Refinement.** Does M-refine improve behavior over a long series beyond what M-recall
  alone gives?
- **RQ6 · Generality.** Do the curves replicate across models and providers under identical
  isolation?

## 5. Hypotheses (proposed; thresholds are fixed in the pre-registration before Phase 2)

The exploratory gaps in §1.3 are optimistic by construction, so the proposed thresholds sit well
below them.

- **H1 · Budget.** On fresh capped runs:
  - T: at a 20-request quota, M-on minus M-off success is at least 20 points.
  - I: at a 6-request quota, R-on minus R-off success is at least 15 points.
  - E: at a 12-request quota, full minus R-off success is at least 20 points.
  - At the generous quota (40), each of these gaps is within ±5 points.
- **H2 · Re-acquisition.** In a sandbox where the rule cannot be re-acquired, M-on minus M-off
  transfer success is at least 30 points at every budget. When the rule can be re-derived from
  the inputs, the M effect appears only at binding budgets, as in H1.
- **H3 · Programmatic context.** Beyond one context window, P-off fails where P-on succeeds;
  within the window, P changes mainly cost.
- **H4 · Delegation.** On parallelizable large-input tasks, S-agent reduces wall-clock time by at
  least 25% or raises success at a binding budget; on small multi-defect fixes it shows no
  reliable effect.
- **H5 · Refinement.** Over a series of 10 or more episodes with verifier feedback, M-refine
  raises late-episode success above M-recall alone. The threshold is set after a pilot.
- **H6 · Generality.** The direction of H1 replicates on a second provider; crossover budgets may
  shift with the model.

## 6. Infrastructure changes (Phase 0, before any new runs)

1. **Land the v1 audit.** Done: the accounting fix, regenerated reports, paper corrections, deck,
   and this plan were pushed on 2026-11-05.
2. **Paper numbers from code.** One script derives every number and table the paper quotes from
   the result files; the paper consumes its output. No hand-copied numbers.
3. **Budget-curve analysis.** Commit a `budget_curves.py` beside the runner: `success@q` and
   budget multipliers by requests, cost, and wall-clock time, per family × treatment, with
   instance-paired bootstrap intervals.
4. **Paired factorial analysis.** Estimate factor effects on instance-blocked contrasts (paired
   bootstrap), and fit log requests / log cost against the factors with instance as a random
   effect if a mixed-model dependency (for example statsmodels) is accepted. Report cost
   multipliers with intervals.
5. **Isolation that holds.**
   - Materialize every cell into its own task directory, keyed by run ID; never reuse a task
     directory across arms.
   - Run each cell in a real sandbox (a container or an OS-level sandbox) whose filesystem holds
     only that episode's inputs. The runner's temporary tree, other cells, and earlier episodes are
     invisible to the agent. A canary file placed outside the sandbox verifies this: any read of
     it fails the run.
   - Write session transcripts where the agent cannot read them; the runner collects them after
     the episode.
   - M-on shares only the harness directory across episodes; HOME, TMPDIR, and working trees are
     fresh per episode, as for M-off.
   - Probe families never write the hidden state to disk.
6. **Runner hardening.**
   - Keep transcripts: compress each cell's session files into the results directory.
   - Freeze the protocol: the first invocation writes the config hash into the results directory,
     and a resume with a different hash fails unless an explicit, recorded override is given.
   - Record the quota and timeout in force, and the uptake metrics of §3.3, on every attempt.
   - Add a leak audit: scan transcripts for file reads outside the episode's own inputs.
7. **Treatment-layer extensions.** P, the M-recall/M-refine split, and S-call if adopted. Each gets
   a matrix test and a model-visible check behind the experiment entry point, landed as its own
   change.
8. **Pre-registration.** A short document fixing hypotheses, metrics, sample sizes, analysis, and
   stopping rules, hashed into the frozen protocol before Phase 2 starts.

## 7. Phased plan

Cost estimates are rough, scaled from v1 per-cell costs (`deepseek-v4-flash` unless noted), and
include a small allowance for transport retries.

### 7.1 Phase 0 · this week · no provider spend

The infrastructure in §6, plus the full budget-curve and factorial re-analysis of v1 data. The
re-analysis is exploratory and is labeled as such in the paper.

### 7.2 Phase 1 · weeks 1–2 · ≈ ¥275–310 · close the validity gaps

All runs are sandboxed and use the frozen protocol.

| Run | Design | Estimate |
| --- | --- | ---: |
| Transfer family re-run | All eight arms × 30 instances (240 cells), run together so pairing and service conditions stay matched | ≈ ¥130 |
| Mechanism check re-run | 40 cells | ≈ ¥20 |
| Hidden-policy probe re-run | 6 series × M-on/M-off, mapping never written to disk | ≈ ¥20 |
| 60-cell repeat re-run | Full vs all-off, ten instances per family | ≈ ¥15 |
| Fixed-quota external re-runs | LongBench-v2 (30 requests), LongMemEval (160), and the boundary check (80), every cell under one quota | ≈ ¥75–110 |
| Strong model, same isolation | Transfer family, full vs all-off, 10 instances | ≈ ¥15 |

**Decision point D1:** if sandboxed T M-off accuracy at the 40-request quota falls by 5 or more
points relative to v1, the v1 M null was leakage. Report it and reframe the transfer result (§1.2
predicts this).

**Decision point D1b:** if the sandboxed hidden-policy M-off arm stays near zero at every budget,
M-recall is irreplaceable for state that cannot be re-acquired (H2).

### 7.3 Phase 2 · weeks 2–5 · ≈ ¥950–1,150 · the dials and the new factors

**Experiment A · budget gradient (confirmatory, tests H1).**

- Families and quotas, chosen where the exploratory curves separate most: T at 15/20/25, I at
  4/6/8, E at 10/12/15, plus the generous 40 as the reference covered by Phase 1.
- Arms: full, R-off, S-agent-off, M-off, all-off; 30 instances per family, every instance under
  every arm and quota.
- Metric: success under the real cap (primary), mean score (secondary), budget multipliers.
- Validation of the counterfactual: compare real capped success with `success@q` from uncapped
  runs per arm and quota. If they agree (absolute difference within 5 points, or the paired
  interval of the difference covers zero), "one uncapped run yields the whole budget curve"
  becomes a methods contribution.
- Optional variant: state the budget in the prompt and test whether a budget-aware agent closes
  the gap by planning differently.
- Estimate: ≈ ¥250–300 (capped cells are cheap).

**Experiment B · re-acquisition gradient (tests H2).**

- One rule family with three re-acquisition conditions:
  - impossible: the rule appears only in the episode-1 prompt, inside the sandbox;
  - re-derivable: later episodes include k labeled examples;
  - re-readable: the rule sits in a file the agent must find.
- M-on vs M-off at two budgets (binding and generous); six-episode series, 20 series per
  condition per arm.
- Estimate: ≈ ¥250–300.

**Experiment C · natural non-derivable state (ecological validity for RQ2).**

- 20–30 multi-session coding scenarios. Session 1 contains a decision or preference that never
  reaches the repository (a code-style rule, a target API version, a team convention, where a
  config lives); later sessions need it.
- M-on vs M-off; the verifier checks that later sessions honor the decision.
- Estimate: ≈ ¥100–150.

**Experiment D · delegation (tests H4).**

- Parallelizable large-input tasks: multi-document synthesis and many-file search or migration.
  v1 hints at this: under LongBench-v2's original 8-request quota, every arm without S ran out of
  budget in 4–5 of 20 cells, and the full harness in none.
- Arms: full vs S-agent-off, plus S-call-off if adopted and all-off as a reference. Wall-clock
  time and peak context tokens are first-class outcomes.
- Estimate: ≈ ¥100.

**Experiment E · programmatic context (tests H3).**

- OOLONG-style length bands from well inside to well beyond one context window.
- P-on vs P-off with R, S, and M on; the same questions in both arms.
- Estimate: ≈ ¥100–150.

**Experiment F · refinement (tests H5).**

- Series of 10–12 episodes on one evolving task type, with verifier feedback after each episode.
- M-recall only vs M-recall + M-refine; the outcome is success over the last third of the series.
- Pilot first to set the H5 threshold.
- Estimate: ≈ ¥150.

**Decision points.** D2: if real capped success departs from the counterfactual beyond the
agreement criterion, report real capped curves only and drop the methods claim. D3: if B shows no
ordering across the three conditions, report what was found and revisit the operationalization.

### 7.4 Phase 3 · weeks 5–7 · ≈ ¥550–850 · generality

- **Second provider:** Experiment A on a subset (one quota per family below the crossover, plus
  the reference), Experiment B's impossible and re-derivable conditions, and Experiment C. The
  estimate depends on that provider's pricing (≈ ¥300–600).
- **`deepseek-v4-pro` on all three families,** under exactly the isolation used for
  `deepseek-v4-flash`, with transcripts (≈ ¥250).
- **Decision point D4:** if the second provider shows no separation, scope the claims to the
  models tested.

### 7.5 Phase 4 · weeks 7–9 · writing

- Restructure the paper around the two dials and the redefined factors. Core figures:
  - success-vs-budget curves per treatment, by requests, cost, and time;
  - budget multipliers per capability, with intervals;
  - the M effect across re-acquisition conditions;
  - P against input size; S-agent latency; M-refine over a series;
  - a cross-model comparison under identical isolation.
- An evaluation-pitfalls section covering:
  - budget exhaustion misclassified as infrastructure and retried;
  - selection effects from mid-run quota changes;
  - shared task directories across arms;
  - session transcripts reachable from ablated arms;
  - a probe whose hidden state sat on disk;
  - the counterfactual budget method and its validation.
- Related work: position against scaffold and interface ablations (for example SWE-agent),
  simpler-pipeline baselines (for example Agentless), recursive language models, and memory and
  skill-library agents (for example MemGPT, Reflexion, Voyager).
- Pick a target venue and schedule backwards from its deadline.

## 8. Budget and timeline

| Phase | Weeks | Provider spend (rough) |
| --- | --- | ---: |
| 0 · infrastructure and re-analysis | this week | 0 |
| 1 · validity re-runs | 1–2 | ≈ ¥275–310 |
| 2 · Experiments A–F | 2–5 | ≈ ¥950–1,150 |
| 3 · generality | 5–7 | ≈ ¥550–850 |
| 4 · writing | 7–9 | 0 |
| **Total** | ≈ 9 weeks | **≈ ¥1,800–2,300** (v1 spent ≈ ¥465) |

## 9. Risks and fallbacks

| Risk | Mitigation or fallback |
| --- | --- |
| HARKing: the budget hypothesis came from v1 data | Pre-register H1–H6 and the analysis before Phase 2; label the v1 re-analysis exploratory |
| Sandboxing is harder than expected (macOS sandboxes, container overhead) | Run the experiment runner on Linux containers; gate every run on the canary check; until the sandbox passes it, no confirmatory run starts |
| The factor extensions (P, M-refine split) touch product code | Keep them behind the experiment entry point with matrix tests, landed as separate small changes |
| Provider instability (v1's main run needed 447 infrastructure retries) | Frozen protocol, per-run hard budget caps, transport retries owned by the proxy |
| Experiment A does not reproduce the counterfactual curves | Report real capped curves; fall back to the cost-multiplier paper, which v1 already supports |
| An off arm still re-acquires state through an unseen channel | Leak audit on transcripts; an M-off arm near M-on under the impossible condition flags it |
| Scope creep | Hold to the minimum viable version below |
| Cost overrun | Each phase has its own hard cap in the runner config; stop and re-plan at a cap |

## 10. Minimum viable version

If time is short: Phase 0, Phase 1, and Experiment A. The sandboxed transfer and hidden-policy
re-runs settle the M question, and Experiment A turns v1's "no accuracy effect" into a
budget-dependent effect with a validated measurement method.

## 11. Deliverables

- Pre-registration document, frozen with the protocol hashes.
- A sandboxed runner (per-cell task directories, canary check, unreachable transcripts, M-on
  sharing only the harness directory) plus the hardening in §6, with tests.
- Treatment-layer extensions for the redefined factors, each with matrix tests and model-visible
  checks.
- Analysis scripts: budget curves and multipliers, paired factorial analysis, paper-number
  generation.
- Results for Phases 1–3 with transcripts, uptake metrics, summaries, and analysis reports.
- Paper v2 built around the two dials and the redefined factors, with the evaluation-pitfalls
  section.

## 12. Responsibility and disclosure

- Exploratory analyses of v1 data are labeled exploratory; confirmatory claims rest on
  pre-registered runs.
- Every protocol change after a run starts is recorded with its reason, and the affected runs are
  re-run in full rather than patched.
- The v1 audit disclosures carry into v2: failure accounting, mid-run quota changes, the runner
  change during the main run, and isolation limits.
- The v1 claims the validity check overturned are corrected in the paper, not footnoted:
  - the M null on transfer;
  - the mechanism and boundary checks' "recoverable from the inputs" explanation;
  - the hidden-policy probe's interpretation;
  - the strong-model "model dependence".
