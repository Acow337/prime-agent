# What Does the Harness Buy?
## A Factorial Ablation of Persistent Execution, Recursive Delegation, and Durable State in RLM-Style Coding Agents

> **DRAFT — provisional results.** Numbers marked *pilot* come from a 120-cell
> calibration run; the 720-cell main experiment is in progress and is not yet
> reported. This draft is an artifact of an ongoing study, not a submission.

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

In a 120-cell pilot (one cheap API model, five instances per family), the
information and engineering families sit at ceiling: all eight treatments solve
them, and only R's cost is visible (R-off uses ~40–50% more tokens, time, and
money for the same accuracy). The transfer family separates one factor: every
M-on cell persisted 2.6–3.1 KB of harness state and scored 1.0 in 19 of 20
cells, while M-off cells succeeded in only 8 of 20 (Fisher exact
* p* ≈ 0.0004; bootstrap 95% CI for the mean-score difference ≈ [0.11, 0.28]).
The fully enabled configuration was simultaneously the most accurate (15/15) and
the cheapest (¥1.87 for its 15 cells). We report these provisional findings,
describe the measurement infrastructure and failure taxonomy that make them
auditable, and discuss why single-episode benchmarks cannot price a harness.

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
3. **A first factorial reading.** On a cheap API model, I and E are at ceiling;
   R costs 40–50% more with no accuracy gain; and M measurably improves
   cross-episode transfer (19/20 vs 8/20) while the fully enabled harness is the cheapest
   accurate configuration.

The rest of the paper follows the plan in the study design: related work (§2),
treatments (§3), benchmark and protocol (§4), component effects (§5),
budget-aware selection (§6, planned), negative transfer and safety (§7),
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

### 5.1 Pilot

The 120-cell pilot (3 families × 5 instances × 8 treatments, cheap API model)
cost ¥26.13 and 20.0M prompt tokens; 119 cells completed and one E cell hit the
240 s timeout. Family aggregates:

| Family | Success | Mean score |
| --- | ---: | ---: |
| I | 40/40 | 1.00 |
| E | 39/40 | 0.98 |
| T | 27/40 | 0.89 |

I is at ceiling for every treatment, so the pilot cannot price R or S from
accuracy. E is near ceiling. The signal is in T.

### 5.2 Transfer

| Arm | Success | Mean score |
| --- | ---: | ---: |
| M-on (persistent harness) | 19/20 | 0.98 |
| M-off (fresh state per episode) | 8/20 | 0.79 |

Fisher exact *p* ≈ 0.0004; bootstrap 95% CI for the mean-score difference
≈ [0.11, 0.28]. Every M-on cell wrote 2.6–3.1 KB of harness state; M-off cells
wrote none. This is consistent with the intended mechanism: a stored convention
is reusable in episode 2 and reconcilable against the changed rule in episode 3,
while a stateless session must re-derive the rule each time.

The pilot T family used a *single* template. The main run replaces it with ten
templates and a five/five positive/negative split; those numbers are
`[main run in progress]`.

### 5.3 Cost of a capability

The fully enabled configuration (`r1-s1-m1`) was both the most accurate arm
(15/15) and the cheapest (¥1.87 for its 15 cells; 9.0 requests per cell). R-off arms paid
40–50% more (e.g. `r0-s1-m1`: ¥3.73, 12.6 requests per cell) for the same
accuracy. S-off and M-off changed prompt size only slightly, consistent with the
removed capability text rather than a behavioural difference on these tasks.

### 5.4 Interactions

`[main run in progress]` — the main matrix reports R×S, R×M, and S×M cells and
a paired analysis across the 30 instances per family.

---

## 6. Budget-Aware Selection

Planned, not yet evaluated. The selector takes a task description and a budget
and predicts the cheapest configuration whose expected success exceeds a
threshold; baselines are the eight fixed configurations plus an oracle and a
random-choice baseline. We will report success-versus-cost Pareto curves on
held-out instances and on the ten unseen transfer templates. This section is
omitted from the present draft's claims.

---

## 7. Negative Transfer and Safety

Five of the ten transfer templates change one threshold in episode 3. An agent
that stored the old rule and blindly reapplies it mislabels the edge cases; the
verifier reports the per-item expected/actual labels, so stale-rule reuse is
visible rather than inferred. In the pilot's single negative template, several
M-off cells lost episode 3 entirely, consistent with rule re-derivation rather
than stale reuse; M-on cells reconciled the stated change. The main run
separates positive from negative templates by construction.

Safety framing: a harness that persists state can persist *wrong* state. Our
treatment layer keeps rollback and provenance in the implementation, but the experiment
does not yet quantify mitigation strategies; we flag this as the most important
follow-up for deployment.

---

## 8. Limitations

* **Synthetic tasks.** I and E are self-built and deterministic. They are
  mechanism tests, not an ecosystem sample; both are at or near ceiling for the
  cheap model, so they cannot establish R or S accuracy effects.
* **One model.** All pilot numbers come from `deepseek-v4-flash`. Strong-model
  replication and a second model are pending.
* **Small n.** Five instances per family in the pilot; the main run uses 30,
  but treatment-level variance is still estimated from a synthetic generator.
* **Provider black box.** Token accounting and streaming behaviour come from the
  provider; service drift is controlled by short time blocks and recorded
  request metadata, not by reproducibility at the token level.
* **Infrastructure noise.** The pilot's wall-clock was dominated by transport
  stalls; failure classification and retries mitigate but do not eliminate this.
* **One implementation.** The testbed is a single production harness; we do not
  claim implementation independence. The factors are defined at capability
  boundaries that any RLM-style harness exposes, but a second implementation
  would strengthen the generalization.
* **Draft status.** The main experiment, repeat runs, strong-model validation,
  selector, and external benchmarks are not yet reflected here.

---

## 9. Conclusion

Decomposing an agent harness into R, S, and M is feasible in a production harness
without changing the default path, and the resulting measurements already show a
shape that aggregate benchmarks hide: on easy, single-episode tasks the harness
is pure cost (R-off is 40–50% more expensive for the same accuracy), while on a
cross-episode task durable state is worth a large accuracy difference. The full
fully enabled configuration was the cheapest accurate arm in the pilot. Whether these
effects survive ten templates, a second model, and real-world tasks is the
subject of the main experiment and follow-up work.

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

## Appendix D — Pilot family × treatment table

Generated by
`python3 experiments/harness-ablation/runner/analyze.py --results .../pilot-120`.
The pilot T family used the single triage template; the main run uses ten.

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
