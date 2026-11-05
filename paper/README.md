# Paper draft

This directory holds the manuscript draft for the harness-ablation study.

- `paper.md` — the full draft (English), following the section plan in
  `docs/superpowers/specs/2026-10-18-harness-ablation-research-design.md` §13.

The draft is framed as a general study of harness components (R = persistent
execution, S = recursive delegation, M = durable state). The concrete
implementation is named only in the artifact appendix, where reproducibility
requires it; the claims do not depend on the product identity.

## Status: DRAFT, complete results, pending submission

The numbers come from the completed 720-cell main matrix (three families x 30
instances x 8 treatments), a 60-cell repeat, the mechanism and boundary checks,
the selector analysis, four external benchmarks (LongBench-v2, SWE-bench
Verified, LongMemEval, OOLONG), the hidden-policy probe, and a strong-model
replication on the transfer family and LongMemEval. A second model provider is
the remaining experiment; the draft states resolution limits rather than
claiming effects the samples cannot support.

## Evidence files

| Artifact | Path |
| --- | --- |
| Treatment implementation | `crates/pa-core/src/session_engine/treatment.rs`, `engine.rs` |
| Kernel reset protocol | `prime-agent-runtime/src/rlm/repl.py`, `crates/pa-core/src/kernel/protocol.rs` |
| Treatment tests | `crates/pa-core/src/session_engine/engine/tests.rs`, `kernel_capture_freshness.rs` |
| Matrix runner | `experiments/harness-ablation/runner/runner.py` |
| Task families | `experiments/harness-ablation/runner/families/{i,e,t}_family.py` |
| Pilot records | `experiments/harness-ablation/runner/results/pilot-120/` |
| Pilot analysis | `python3 experiments/harness-ablation/runner/analyze.py --config .../config.pilot.json --results .../pilot-120` |
| Frozen protocol | `experiments/harness-ablation/runner/frozen-pilot.json`, `frozen-main.json` |
| Main analysis | `experiments/harness-ablation/runner/results/main-720/analysis.md` |
| Selector | `experiments/harness-ablation/runner/selector.py`, `results/main-720/selector.md` |
| Equivalence summary | `experiments/harness-ablation/runner/equivalence.py`, `results/equivalence.md` |
| Repeats | `experiments/harness-ablation/runner/results/repeats-60/` |
| Mechanism check | `experiments/harness-ablation/runner/families/t_mech_family.py`, `results/mech-40/` |
| Boundary check | `experiments/harness-ablation/runner/results/boundary-20/` |
| LongBench-v2 | `experiments/harness-ablation/runner/families/lb_family.py`, `results/external-lb/` |
| LongMemEval | `experiments/harness-ablation/runner/families/lme_family.py`, `results/external-lme/` |
| SWE-bench | `experiments/harness-ablation/external/swebench_driver.py`, `external/results-12/` |
| OOLONG | `experiments/harness-ablation/runner/families/ol_family.py`, `results/external-ol-lengths/` |
| Hidden-policy probe | `experiments/harness-ablation/runner/families/hl_family.py`, `results/hidden-series/` |
| Strong-model replication | `experiments/harness-ablation/runner/results/pro-t/`, `results/pro-lme/` |

## Reproducing

```bash
# main matrix (720 cells)
python3 experiments/harness-ablation/runner/runner.py \
  --config experiments/harness-ablation/runner/config.main.json \
  --binary target/debug/prime-agent \
  --results experiments/harness-ablation/runner/results/main-720 --mode live
python3 experiments/harness-ablation/runner/analyze.py \
  --config experiments/harness-ablation/runner/config.main.json \
  --results experiments/harness-ablation/runner/results/main-720
python3 experiments/harness-ablation/runner/selector.py \
  --config experiments/harness-ablation/runner/config.main.json \
  --results experiments/harness-ablation/runner/results/main-720 \
  --out experiments/harness-ablation/runner/results/main-720/selector.md

# equivalence summary (run from experiments/harness-ablation/runner)
python3 equivalence.py \
  --main results/main-720 --main-config config.main.json \
  --longbench results/external-lb --longbench-config config.external.json \
  --longmemeval results/external-lme --longmemeval-config config.lme.json \
  --out results/equivalence.md

# external benchmarks
python3 experiments/harness-ablation/runner/runner.py \
  --config experiments/harness-ablation/runner/config.external.json \
  --binary target/debug/prime-agent \
  --results experiments/harness-ablation/runner/results/external-lb --mode live
# LongBench-v2 and LongMemEval data files are fetched per the module docstrings.
```
