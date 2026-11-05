# The Harness Ledger
## What Persistent Execution, Recursive Delegation, and Durable State Cost — and When They Pay Off

> **DRAFT — complete results, pending submission.** The numbers come from the
> 720-cell main matrix, a 60-cell repeat, three external benchmarks
> (LongBench-v2, SWE-bench Verified, LongMemEval), and a boundary-sensitivity
> check, plus a strong-model replication on the transfer family and
> LongMemEval. This draft is an artifact of an ongoing study, not a submission.

**Anonymous authors — under review.**

---

## Abstract

Coding agents are increasingly shipped as *harnesses*: a persistent execution
environment, recursive subagent spawning, and durable state that the agent can
read and refine across turns and sessions. Each capability adds prompt surface,
tokens, and failure modes, yet the causal contribution of each is rarely
measured in isolation. We use a production RLM-style coding-agent harness as a
testbed and decompose the capabilities such harnesses share into three
orthogonal binary factors: **R** (the execution namespace persists across
settled turns), **S** (the agent may delegate to recursive child agents), and
**M** (durable harness state may be read, injected, and refined). We implement
all eight R/S/M configurations behind an experiment-only entry point that leaves
the default operating path unchanged, and we evaluate them on three task
families: multi-table information management (I), small multi-module bug fixing
(E), and cross-episode transfer (T).

We evaluate all eight configurations on 30 instances per family (720 cells, one
cheap API model). On this suite we detect **no accuracy effect** from any capability:
every treatment solves 84-88 of 90 cells, and the transfer comparison is 115/120
for M-on versus 117/120 for M-off (Fisher exact *p* = 0.72; bootstrap 95% CI for
the mean-score difference [-0.010, 0.015], so effects above 1.5 points are
ruled out). What the harness changes is **cost**:
the fully enabled configuration is the cheapest arm (¥16.87 for its 90 cells),
turning R off raises provider requests by 14-41% depending on the family, and
turning M off roughly doubles the transfer family's spend because every episode
must re-derive the rule. A 120-cell pilot on a single transfer template had
suggested a large M accuracy effect (19/20 vs 8/20); that effect did not survive
ten templates, and we report a mechanism check that isolates whether it returns
when the rule cannot be guessed. Three external checks are consistent with the
same picture, though coarser: on LongBench-v2 the arms range 0.65-0.80 with no
significant ordering; on SWE-bench Verified the full harness and the all-off
harness each resolve 10 of 12 repositories (same unresolved pair); and on
LongMemEval the M-off arm is not worse (12/20 versus 11/20). At n=20 the external
minimum detectable effect is about 30 points, so they rule out only large
effects. The result reframes the harness contract for this task distribution:
capabilities buy cheaper repeated work, not better single answers. A
hidden-policy probe pins down the exception: when the mapping is stated once,
cannot be inferred from the inputs, and must survive across episodes, M-on
scores 0.936 against M-off's 0.414 (95% CI [0.352, 0.720]). The accuracy
benefit is real, but conditional on non-derivable state and on the model: with
`deepseek-v4-pro` the transfer gap reopens (0.975 versus 0.710).

---

## 1. Introduction

Agent "harnesses" bundle capabilities that are individually plausible but rarely
isolated: a persistent interpreter, the ability to spawn subagents, and state
that survives compaction and session boundaries. In practice these features are
added together, benchmarked together, and justified by aggregate scores. That
practice cannot answer the question a budget-constrained user actually asks:
*which capability pays for itself, on what kind of task, and at what cost?*

We address the question with a controlled ablation inside a production
RLM-style harness. Two abstractions that such harnesses instantiate motivate the
factors: the **recursive language model (RLM)** pattern treats context as a
variable and subagents as function calls inside a persistent interpreter, and
the **continual harness** stores prompts, memories, skill descriptions, and
subagent specifications as durable state refined through small, evidence-backed
updates. The unit of analysis is the harness component, not the tool: we use one
production implementation as the instrument, keep it unnamed in the claims, and
name it only where reproducibility requires it (Appendix C).

We make three contributions:

1. **A typed, deployment-safe treatment layer.** R, S, and M become independent
   binary switches with stable IDs (`r1-s1-m1` … `r0-s0-m0`). Every switch is
   implemented at the boundary where the capability is admitted, not by prompt
   text alone, and the default entry point always resolves the fully enabled harness.
2. **An auditable measurement harness.** An eight-configuration matrix runner
   with per-cell quotas, per-cell usage and latency records, transport-level
   retries with failure classification, resumable JSONL records, and frozen
   protocol hashes.
3. **A factorial reading of 720 cells.** Across three families the R/S/M
   factors show no detected accuracy effect on this suite; the durable-state comparison is
   115/120 versus 117/120. Capability costs are large and opposite in sign to the
   pilot's apparent benefit: the fully enabled harness is the cheapest accurate
   arm, R-off raises requests by 14-41%, and M-off roughly doubles transfer
   spend. A single-template pilot's large M effect does not replicate, which
   motivates a non-guessable-rule mechanism check.

The rest of the paper follows the plan in the study design: related work (§2),
treatments (§3), benchmark and protocol (§4), component effects (§5),
budget-aware selection (§6), negative transfer and safety (§7),
limitations (§8), and conclusion (§9).

---

## 2. Related Work

**Agent harnesses.** Contemporary coding agents combine a shell, a code
interpreter, tool dispatch, and context management. Benchmarks such as
SWE-bench measure end-to-end issue resolution and therefore conflate the model
with its harness; harness ablations are usually limited to single features
(e.g., removing a tool) rather than orthogonal factors.

**RLMs and programmatic tool use.** The RLM formulation treats long context as
data inside a persistent REPL and turns subagents into programmatic calls. It
predicts that persistence and recursion matter most when a task exceeds a
single context window or decomposes into independent subtasks — a prediction our
I and E results do not yet stress, because those tasks fit in one or two turns.

**Continual harness / memory.** Refinement systems persist lessons as
supplemental prompts, memories, skills, or subagent specifications. The claim
that such state helps is usually evaluated within one session. We evaluate the
cross-session claim directly with an episode series whose later episodes withhold
the rule (M-on can reuse a stored convention; M-off cannot).

**Test-time compute and budget-aware selection.** Prior work studies how much
inference to buy; we study *which harness* to buy and report cost alongside
accuracy, which is the input a selector needs.

---

## 3. Harness Treatments

### 3.1 Definitions

| Factor | On | Off |
| --- | --- | --- |
| **R** — REPL persistence | Python namespace survives a settled model turn | namespace is cleared at every settled turn; within-turn execution is unaffected |
| **S** — recursive subagents | `rlm.spawn`/`rlm.create_session` may create descendants | child admission is refused at the host boundary; the prompt drops the capability |
| **M** — persistent harness | harness state is read, injected, and refined | the experiment agent directory pins the harness gate off, no digest is injected, and direct harness writes are refused |

The factors are orthogonal by construction: the implementation resolves them
once at session assembly and each has a single admission boundary.

### 3.2 Implementation

* **R** installs a `TurnEnd` listener that calls the kernel's
  `reset_namespace` protocol operation. The runtime clears user bindings while
  preserving runtime bindings (`rlm`, `bash`, `asyncio`, builtins); the manager
  classifies the reset as a namespace mutation so snapshot freshness is
  invalidated. A reset failure fails the run.
* **S** withholds the child host from the session wiring, forces
  `allow_recursion = false`, and drops the spawn capability from the layered
  system prompt. The kernel's own fail-closed host then answers
  `no RLM child runtime`.
* **M** pins the isolated agent directory's `harnessRefinements` gate, skips
  digest injection, and removes the refine skill so the prompt advertises no
  refused capability. A normal `create_session` never writes this setting.

The shipped CLI has no treatment flag. Experiment hosts select a treatment
through `create_session_with_treatment`; the headless runner sets
`PRIME_AGENT_HARNESS_TREATMENT` to one of the eight stable IDs and an unknown
value fails loudly.

### 3.3 Identifiability and fairness

Treatment validity is checked at the model-visible surface, not only in unit
tests: a two-cell `python_repl` run keeps a value across turns under R-on and
reads `missing` under R-off; a counting host records zero spawns under S-off and
the kernel returns the expected error; under M-off a direct
`rlm.harness.create_memory` call is refused with the `/settings` error while a
normal session preserves the user's saved setting. All eight combinations are
constructed in one matrix test that asserts the stable ID, the prompt surface,
and the resolved gate.

Budget is shared across treatments: the same model, prompt layer, tool set,
compaction policy, task fixtures, and verifier are used; each cell carries the
same per-cell request quota.

---

## 4. Benchmark and Protocol

### 4.1 Task families

| Family | Construction | Primary factor |
| --- | --- | --- |
| **I** — information management | three joined tables (orders, customers, refunds), a date window, a gold-tier join, weekday aggregation, a refund-customer total, an exclusive-or contradiction count, and an earliest-date field; six checked fields; refunds are authoritative over order status | R |
| **E** — engineering | an orders package (`parser`, `pricing`, `report`) with six independent defects (comment lines, half-up rounding, zero-net customers, duplicate ids, negative amounts, report ordering); hidden tests are verifier-owned and the score is the hidden-test pass ratio | S |
| **T** — transfer | a three-episode series: episode 1 states a classification rule and asks for a durable harness note (unscored); episode 2 withholds the rule and scores transfer; episode 3 is scored. Ten templates span triage, SLA, risk, capacity, fraud, quality, routing, retention, escalation, and compliance; five keep the rule (positive transfer) and five change one threshold (negative transfer) | M |

### 4.2 Design and metrics

Each family has 30 instances for the main experiment (five during the pilot),
and every instance is crossed with all eight treatments. Instances are paired
blocks: the same task receives every treatment, and treatment order is fixed by
the runner so a task's cells share the day's service conditions. Metrics:
task success (verifier-defined), fractional score, provider requests, prompt and
completion tokens, wall-clock latency, and actual cost. Failures are classified
as *agent* failures (wrong answer, budget exhausted) versus *infrastructure*
failures (transport errors, timeouts), and infrastructure cells are retried
rather than counted.

### 4.3 Budget and failure policy

Each cell has a request quota; the run has a settled-cost gate that stops when
*actual cost + the next request's worst-case bound* would cross the hard cap.
A connection failure that produced no answer is re-sent at most twice, each
re-send charged to the conservative bound; HTTP answers are never retried. An
interrupted run resumes from its JSONL records without repeating completed
cells.

### 4.4 Frozen protocol

Before the main run, the configuration, runner, family modules, and one digest
per materialized instance are hashed into `frozen-main.json`; a rerun is
comparable only when those hashes match.

---

## 5. Component Effects

### 5.1 The main matrix

The main run crosses 30 instances per family with all eight treatments and
completes after retrying transport failures: 720 cells, 448 infrastructure
retries, and ¥213.93 of provider spend total (the tables below count completed
cells only, ¥205.07). The suite is not saturated for the model — E and T still
fail a few cells — but individual cells differ in weight: the I family uses about
four requests per cell, T about twenty.

| treatment | success | mean score | requests/cell | cost (¥) |
| --- | ---: | ---: | ---: | ---: |
| `r1-s1-m1` (full) | 85/90 | 0.991 | 10.5 | 16.87 |
| `r1-s1-m0` | 84/90 | 0.988 | 13.0 | 25.85 |
| `r1-s0-m1` | 87/90 | 0.995 | 11.6 | 22.97 |
| `r1-s0-m0` | 88/90 | 0.997 | 12.9 | 26.25 |
| `r0-s1-m1` | 86/90 | 0.993 | 13.3 | 23.02 |
| `r0-s1-m0` | 86/90 | 0.989 | 15.7 | 32.12 |
| `r0-s0-m1` | 87/90 | 0.995 | 13.7 | 25.73 |
| `r0-s0-m0` | 86/90 | 0.992 | 16.2 | 32.27 |

### 5.2 No detected accuracy effect, with stated resolution

Across all eight treatments the success spread is 84-88 of 90 cells and the mean
score spread is 0.988-0.997. The I family is at ceiling in every arm (29/30); E
ranges 26-30; T is saturated as well. For the transfer family, where the pilot
had shown a large gap:

| Arm | Success | Mean score |
| --- | ---: | ---: |
| M-on | 115/120 | 0.9929 |
| M-off | 117/120 | 0.9908 |

Fisher exact *p* = 0.72; bootstrap 95% CI for the mean-score difference
[-0.010, 0.015]. A two one-sided test against a 2-point equivalence margin
rejects a difference of that size, and the minimum detectable effect is 1.3
points: this comparison rules out effects larger than about 1.5 points, which is
a real bound rather than just a non-significant test. Smaller effects are not
excluded, and the pilot's effect is not replicated. A 60-cell repeat of the
full-versus-off contrast (ten instances per family) reproduced 55 of 60 scores
exactly; the five changes were partial-credit shifts, not success flips, and the
means are 0.9897 ± 0.039 (full) versus 0.9917 ± 0.046 (off).

### 5.3 Cost is where the harness shows up

The fully enabled harness is the cheapest accurate arm at ¥16.87 per 90 cells
and 10.5 requests per cell. Disabling R raises requests by 41% (I), 34% (E), and
14% (T); the most expensive arm (`r0-s0-m0`) costs roughly twice the full one.
The transfer family shows the same pattern for M: with R on, M-off costs ¥16.79
versus ¥9.11 for M-on; with R off, ¥22.93 versus ¥10.26. The mechanism is
mundane and measurable: without durable state every episode re-derives the
convention, so the model spends more turns to reach the same answer.

### 5.4 Why the pilot said otherwise

The pilot used a single transfer template and found 19/20 versus 8/20. With ten
templates the gap disappears. We treat the pilot as calibration, not evidence:
single-template transfer effects are fragile, and a round threshold (5%, 800 ms)
is plausibly guessable even without memory.

A 40-cell mechanism check replaced the round thresholds with arbitrary
per-instance values (e.g. 716 bp / 744 ms), repeated them in no episode after the
first, and gave M-off episodes a fresh HOME, TMPDIR, and session directory so a
note could not be smuggled through files. Accuracy is again at ceiling in all
eight arms (40/40), but the cost split holds: M-on arms use 14-20 requests and
¥0.95-2.31 per five tasks, M-off arms 22-27 requests and ¥2.04-4.05. The
remaining confound is the row distribution: most rows sit far from the
thresholds, so an approximate rule still labels them correctly. A 20-cell
boundary variant put every row within two basis points of a threshold; M-off
still scored at ceiling (20/20 cells at 1.0). The reason is that the row
values themselves bracket the thresholds: an agent that can label the data
can recover the boundary from the inputs, so the exact convention is
identifiable without memory. Making memory necessary would require the rule
to depend on information absent from the inputs, not merely on fine-grained
thresholds.

### 5.5 Interactions

With every cell at or near ceiling, accuracy interactions are not estimable on
this suite; the matrix's value is the cost decomposition, where R and M compound
(the two-factor-off arms are the most expensive).

---

### 5.6 External benchmarks

Three public benchmarks test whether the no-detected-effect result survives
outside the self-built suite.

| Benchmark | Setup | Full harness | All off |
| --- | --- | ---: | ---: |
| LongBench-v2 | 20 long-context multiple-choice questions, contexts 57k-170k chars, four arms | 0.700 | 0.650 |
| SWE-bench Verified | 12 repositories, agent patch scored by the official harness | 10/12 resolved | 10/12 resolved |
| LongMemEval | 10 memory questions, read/read/QA episodes | 11/20 (M-on) | 12/20 (M-off) |
| OOLONG | 11 context-length bands, 1k-4M tokens, R-on vs R-off | 11/11 correct | 11/11 correct |

SWE-bench's resolved sets are identical: both arms fix astropy, matplotlib,
seaborn, flask, requests, xarray, pylint, scikit-learn, sphinx, and sympy, and
both miss django and pytest. LongMemEval is the proxy for M: the two reading
episodes ask the agent to record durable facts, the QA episode withholds the
transcripts, and the normalized-containment score is 0.55 for M-on versus 0.60
for M-off (40/40 cells completed). LongBench differences (0.65-0.80 across four arms) are within noise
at n=20.

OOLONG covers the length axis directly: one task per band from 1k to 4M tokens,
with R on and off. Every completed cell is correct in both arms, including the
4M-token context, which a coding agent solves by scripting over the file rather
than reading it. Cost varies without a stable direction (R-off is cheaper at
131k, 262k, 524k, and 2M; R-on is cheaper at 8k, 16k, and 32k; at 1M both pay
several yuan). Length alone therefore does not make persistent state necessary;
what matters is whether the task forces state to survive *across* turns.

The consistent picture is that on these distributions we detect no accuracy
effect; the largest cost differences still come from the synthetic matrix (R-off
+14-41% requests, M-off about double the transfer spend). The external samples
are small, though, and the resolution is correspondingly coarse: at n=20 the
minimum detectable effect is about 30 points on LongBench and LongMemEval, so
those two rule out only large differences and are consistent with, not
confirmatory of, equivalence. We read the pattern as a property of the tasks as
much as of the harness: none of them make re-derived state expensive enough for
persistence to pay off in correctness.

### 5.7 When memory does pay: a hidden-policy probe

Every family above lets an agent recover the rule from its inputs, which is why
the external benchmarks cannot separate M. To test the opposite condition we
built a probe in which twelve opaque codes map to routing queues through an
arbitrary table that is stated once, is never written to a file a later episode
can read, and cannot be inferred from the codes. Episode 1 states the mapping
and asks for durable memory; the remaining episodes score ticket batches.
Instances cover 3, 6, and 12 episodes.

| episodes | M-on | M-off |
| --- | --- | --- |
| 3 | 1.00, 1.00 | 0.50, 0.00 (timeout) |
| 6 | 1.00, 0.80 | 0.67 (timeout), 0.50 |
| 12 | 0.91, 0.91 | 0.55, 0.27 |

M-on averages 0.936 and M-off 0.414 (bootstrap 95% CI for the difference
[0.352, 0.720]); M-off also spends more (¥1.17-3.82 per cell versus ¥0.18-1.04)
because it searches without finding the table. Timeouts count as failures
because the budget is part of the task.

This is the condition under which durable state converts into correctness: the
state is not derivable from the inputs *and* must survive across episodes. The
external results are not contradicted; they sit on the other side of that
boundary.

### 5.8 Model dependence

The null on the cheap model does not transfer to a stronger one. Running the
transfer family with `deepseek-v4-pro` (ten instances, the same templates):

| model | full harness | all off |
| --- | ---: | ---: |
| `deepseek-v4-flash` | 0.993 (120 cells) | 0.991 (120 cells) |
| `deepseek-v4-pro` | 0.975 (10) | 0.710 (10) |

For the strong model the gap is 0.265 (bootstrap 95% CI [0.080, 0.470]), and the
all-off arm also costs four times more (¥10.04 versus ¥2.50 for ten tasks). The
failure pattern is not confined to the negative templates: without memory the
strong model also misses positive-transfer rules (0.05, 0.30, and 0.70 on three
of them), which suggests it does not reconstruct the withheld conventions as
readily as the cheap model does.

The same model does not gain accuracy on LongMemEval, where the memory protocol
leaves more room for guessing:

| benchmark | model | M-on | M-off |
| --- | --- | ---: | ---: |
| T transfer | `deepseek-v4-flash` | 0.993 | 0.991 |
| T transfer | `deepseek-v4-pro` | 0.975 | 0.710 |
| LongMemEval | `deepseek-v4-flash` | 0.55 | 0.60 |
| LongMemEval | `deepseek-v4-pro` | 0.333 | 0.444 |

The strong model's LongMemEval cells cost ¥3.06 with memory versus ¥15.15
without it (18/20 completed; two transport losses), so even where accuracy does
not move, persistence cuts the bill by five. Whether a capability pays therefore
depends on the task *and* the model, not only on the task.

## 6. Budget-Aware Selection

The selector takes a budget and chooses one treatment per family. With the main
matrix we can evaluate the policy class directly: estimate each family-treatment
success and cost, enumerate all 512 per-family policies, and keep the Pareto
frontier. This is in-sample (the policy is scored on the instances that produced
the estimates), so it describes the trade-off's shape rather than a held-out
gain.

| policy | cost (¥/90 tasks) | mean score |
| --- | ---: | ---: |
| always full | 16.87 | 0.9911 |
| always off | 32.27 | 0.9916 |
| oracle per family | 26.04 | 0.9981 |

The frontier has seven points. Its cheapest point, ¥16.82, is the full harness
minus S on the information family, and it matches the full harness score; the
most expensive point (¥25.98) reaches 0.9981. In other words, the configuration
that is already the default is cost-optimal, and spending up to 54% more buys at
most 0.7 percentage points of mean score, which is inside the noise of 90 cells.
A held-out selector would therefore be predicting cost, not accuracy — the same
conclusion as the component analysis.

## 7. Negative Transfer and Safety

Five of the ten transfer templates change one threshold in episode 3, and the
prompt states the change explicitly. The main matrix does not show a stale-rule
penalty: negative-template cells score like positive ones (T mean 0.983-1.0
across arms), which suggests the agents read the stated change rather than
blindly reapplying a stored rule. A harness that can persist *wrong* state
remains a real risk; our verifier reports per-item expected/actual labels so
stale reuse would be visible if it occurred, and the treatment layer keeps
rollback and provenance in the implementation.

The pilot's apparent M effect does not survive template diversity (§5.4), and
the completed mechanism and boundary checks show why: when the rule can be
recovered from the inputs, memory is not needed for accuracy. We therefore treat
any future positive result as conditional on non-derivable state, not as a
general accuracy benefit.

---

## 8. Limitations

* **Ceiling effects.** I, E, and T all sit at or near ceiling for the cheap
  model. Accuracy effects of R and S are therefore not identifiable on this
  suite; the study prices the capabilities in cost, not accuracy, and any
  accuracy claim needs harder tasks.
* **Synthetic tasks.** All three families are self-built and deterministic.
  They are mechanism tests, not an ecosystem sample; external validity needs
  public benchmarks.
* **Two models, four checkpoints.** The main matrix and the external benchmarks
  use `deepseek-v4-flash`; `deepseek-v4-pro` replicates only the transfer family
  and LongMemEval. A second provider and an independent checkpoint are pending.
* **One implementation.** The testbed is a single production harness; we do not
  claim implementation independence.
* **Infrastructure noise.** The main run needed 448 transport retries and the
  effective spend (¥213.93) is above the completed-cell spend (¥205.07).
  Failure classification and per-cell retries mitigate but do not eliminate
  this, and the wall-clock cost was dominated by stalls rather than model
  compute.
* **Resolution of the null.** The transfer comparison rules out effects larger
  than about 1.5 points, but the external benchmarks have a minimum detectable
  effect near 30 points at n=20, and SWE-bench's twelve paired instances span
  roughly ±27 points. "No detected effect" is therefore a bounded statement, not
  a proof of equivalence on those benchmarks.
* **Probe caveats.** The hidden-policy result is a custom probe with six cells
  per arm and timeouts counted as failures; the strong-model replication covers
  only ten transfer instances. Their sizes, not their directions, are the weak
  point.
* **Open work.** Run-to-run variance is bounded by the 60-cell repeat (55/60
  identical, five partial-credit changes), but that repeat covers only the
  full-versus-off contrast. The selector here is in-sample, the external
  benchmarks are small subsets, and a second model/provider has not been run.

---

## 9. Conclusion

Decomposing an agent harness into R, S, and M is feasible in a production system
without changing the default path, and the full eight-configuration matrix gives
a result that aggregate benchmarks hide: on a 720-cell suite we detect no
accuracy effect from the capabilities while their costs differ by a factor of
two. The fully
enabled harness is the cheapest accurate arm; turning R off raises requests by
14-41%, and turning M off roughly doubles transfer spend because every episode
must re-derive the convention. A single-template pilot had suggested a large
accuracy benefit for durable state; it did not replicate across ten templates,
which is exactly the kind of fragility that single-episode evaluations cannot
see. Three external benchmarks (LongBench-v2, SWE-bench Verified, and
LongMemEval) point the same way: no detected accuracy difference between the
full harness and the all-off configuration, including on a benchmark built to
test durable memory, though their samples are too small to confirm equivalence. On tasks whose state can be re-derived, the harness's measured value is cheaper
repeated work; when the same state cannot be derived and must survive episodes,
the capability converts into a large accuracy difference (0.936 versus 0.414 in
the hidden-policy probe, and 0.975 versus 0.710 for a strong model on transfer).
The boundary is therefore drawn by both the task and the model, not by the
harness alone.

---

## Appendix A — Treatment IDs

`r1-s1-m1` fully enabled harness; `r1-s1-m0`, `r1-s0-m1`, `r1-s0-m0`, `r0-s1-m1`,
`r0-s1-m0`, `r0-s0-m1`, `r0-s0-m0` as defined in §3.1. The normal CLI never
selects any of them.

## Appendix B — Prompt and verifier shapes

* I: six-field JSON; truth computed by the generator from the same rules.
* E: `SPEC.md` plus a public test; seven verifier-owned hidden tests; score is
  the pass ratio; the reference fix scores 7/7 and the unmodified repository
  scores 0/7 on all instances.
* T: one JSON object per episode keyed by row id; episode 2 and 3 are scored;
  episodes share an agent directory under M-on and use a fresh one under M-off.

## Appendix C — Artifact map

Implementation artifact: the study was instrumented inside Prime Agent, an
open-source RLM-style coding agent (the implementation is named here only for
reproducibility; the claims concern harness components, not the product). See
`paper/README.md` for the file list, reproduction commands, and the frozen hash
files.

## Appendix D — Main matrix and pilot tables

Main matrix (720 cells, completed cells only; transfer effect computed on the
T family):

| treatment | success | mean score | requests/cell | cost (¥) |
| --- | ---: | ---: | ---: | ---: |
| `r1-s1-m1` | 85/90 | 0.991 | 10.5 | 16.87 |
| `r1-s1-m0` | 84/90 | 0.988 | 13.0 | 25.85 |
| `r1-s0-m1` | 87/90 | 0.995 | 11.6 | 22.97 |
| `r1-s0-m0` | 88/90 | 0.997 | 12.9 | 26.25 |
| `r0-s1-m1` | 86/90 | 0.993 | 13.3 | 23.02 |
| `r0-s1-m0` | 86/90 | 0.989 | 15.7 | 32.12 |
| `r0-s0-m1` | 87/90 | 0.995 | 13.7 | 25.73 |
| `r0-s0-m0` | 86/90 | 0.992 | 16.2 | 32.27 |

### Pilot family × treatment table

The pilot (120 cells) used the single triage template; the main run uses ten.
Generated by
`python3 experiments/harness-ablation/runner/analyze.py --results .../pilot-120`.

| family | treatment | n | success | mean score | requests | prompt tok | cost CNY |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| e | r0-s0-m0 | 5 | 5/5 | 1.0 | 13.2 | 812047 | 1.1289 |
| e | r0-s0-m1 | 4 | 4/4 | 1.0 | 13 | 1208757 | 1.5755 |
| e | r0-s1-m0 | 5 | 5/5 | 1.0 | 18.2 | 1840194 | 2.3924 |
| e | r0-s1-m1 | 5 | 5/5 | 1.0 | 15.2 | 1374574 | 1.8211 |
| e | r1-s0-m0 | 5 | 5/5 | 1.0 | 9 | 471153 | 0.6686 |
| e | r1-s0-m1 | 5 | 5/5 | 1.0 | 10.8 | 653522 | 0.9167 |
| e | r1-s1-m0 | 5 | 5/5 | 1.0 | 11.6 | 705754 | 1.0025 |
| e | r1-s1-m1 | 5 | 5/5 | 1.0 | 9.6 | 562717 | 0.8072 |
| i | r0-s0-m0 | 5 | 5/5 | 1.0 | 5 | 252728 | 0.3339 |
| i | r0-s0-m1 | 5 | 5/5 | 1.0 | 5.2 | 273463 | 0.3656 |
| i | r0-s1-m0 | 5 | 5/5 | 1.0 | 5 | 277460 | 0.3705 |
| i | r0-s1-m1 | 5 | 5/5 | 1.0 | 5.6 | 321631 | 0.4224 |
| i | r1-s0-m0 | 5 | 5/5 | 1.0 | 3.8 | 190746 | 0.2506 |
| i | r1-s0-m1 | 5 | 5/5 | 1.0 | 3.6 | 184997 | 0.2428 |
| i | r1-s1-m0 | 5 | 5/5 | 1.0 | 4.4 | 221161 | 0.2892 |
| i | r1-s1-m1 | 5 | 5/5 | 1.0 | 4 | 211879 | 0.2763 |
| t | r0-s0-m0 | 5 | 2/5 | 0.79 | 21.4 | 1649847 | 2.0857 |
| t | r0-s0-m1 | 5 | 5/5 | 1.0 | 17.2 | 1174462 | 1.4915 |
| t | r0-s1-m0 | 5 | 3/5 | 0.87 | 22.2 | 1692881 | 2.1389 |
| t | r0-s1-m1 | 5 | 4/5 | 0.93 | 17 | 1168456 | 1.4894 |
| t | r1-s0-m0 | 5 | 1/5 | 0.67 | 17.2 | 1387997 | 1.751 |
| t | r1-s0-m1 | 5 | 5/5 | 1.0 | 14.4 | 999427 | 1.2767 |
| t | r1-s1-m0 | 5 | 2/5 | 0.82 | 20 | 1555467 | 1.9651 |
| t | r1-s1-m1 | 5 | 5/5 | 1.0 | 13.4 | 614778 | 0.7909 |

Transfer-effect section of the same report:

- M-on: 19/20 success, mean score 0.9825
- M-off: 8/20 success, mean score 0.7875
- Fisher exact p = 0.00043
- bootstrap 95% CI for the mean-score difference: [0.108, 0.280]
## Appendix E — Equivalence summary

Bootstrap 95% intervals (4000 resamples); the minimum detectable effect (MDE) is
the interval half-width. A two one-sided test is applied against the margin.

| comparison | n (a/b) | mean a | mean b | diff | 95% CI | MDE | margin | equivalent |
| --- | ---: | ---: | ---: | ---: | --- | ---: | ---: | --- |
| T: M-on vs M-off | 120/120 | 0.993 | 0.991 | +0.002 | [-0.009, +0.015] | 0.013 | 0.02 | yes |
| LongBench: full vs all-off | 20/20 | 0.700 | 0.650 | +0.050 | [-0.250, +0.350] | 0.300 | 0.10 | no |
| LongMemEval: M-on vs M-off | 20/20 | 0.550 | 0.600 | -0.050 | [-0.350, +0.250] | 0.300 | 0.10 | no |
| SWE-bench: full vs all-off | 12/12 | 10/12 | 10/12 | 0 | (exact) | 0.27 | — | not tested |

The transfer comparison is the only one with enough cells to support an
equivalence claim; the external benchmarks are directional checks.

---

