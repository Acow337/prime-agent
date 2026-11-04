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
the selector analysis, and four external benchmarks (LongBench-v2, SWE-bench
Verified, LongMemEval, plus the boundary check). Strong-model replication is the
remaining experiment; the draft states resolution limits rather than claiming
effects the samples cannot support.

## Evidence files

| Artifact | Path |
| --- | --- |
| Treatment implementation | `crates/pa-core/src/session_engine/treatment.rs`, `engine.rs` |
| Kernel reset protocol | `prime-agent-runtime/src/rlm/repl.py`, `crates/pa-core/src/kernel/protocol.rs` |
| Treatment tests | `crates/pa-core/src/session_engine/engine/tests.rs`, `kernel_capture_freshness.rs` |
| Matrix runner | `experiments/harness-ablation/runner/runner.py` |
| Task families | `experiments/harness-ablation/runner/families/{i,e,t}_family.py` |
| Pilot records | `experiments/harness-ablation/runner/results/pilot-120/` |
| Pilot analysis | `python3 experiments/harness-ablation/runner/analyze.py --results .../pilot-120` |
| Frozen protocol | `experiments/harness-ablation/runner/frozen-pilot.json`, `frozen-main.json` |
| Main analysis | `experiments/harness-ablation/runner/results/main-720/analysis.md` |
| Selector | `experiments/harness-ablation/runner/selector.py`, `results/main-720/selector.md` |
| Equivalence summary | `experiments/harness-ablation/runner/equivalence.py`, `results/equivalence.md` |
| Repeats | `experiments/harness-ablation/runner/results/repeats-60/` |
| Boundary check | `experiments/harness-ablation/runner/results/boundary-20/` |
| LongBench-v2 | `experiments/harness-ablation/runner/families/lb_family.py`, `results/external-lb/` |
| LongMemEval | `experiments/harness-ablation/runner/families/lme_family.py`, `results/external-lme/` |
| SWE-bench | `experiments/harness-ablation/external/swebench_driver.py`, `external/results-12/` |

## Reproducing

```bash
# main matrix (720 cells)
python3 experiments/harness-ablation/runner/runner.py \
  --config experiments/harness-ablation/runner/config.main.json \
  --binary target/debug/prime-agent \
  --results experiments/harness-ablation/runner/results/main-720 --mode live
python3 experiments/harness-ablation/runner/analyze.py \
  --results experiments/harness-ablation/runner/results/main-720

# external benchmarks
python3 experiments/harness-ablation/runner/runner.py \
  --config experiments/harness-ablation/runner/config.external.json \
  --binary target/debug/prime-agent \
  --results experiments/harness-ablation/runner/results/external-lb --mode live
# LongBench-v2 and LongMemEval data files are fetched per the module docstrings.
```
