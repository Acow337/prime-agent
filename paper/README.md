# Paper draft

This directory holds the manuscript draft for the harness-ablation study.

- `paper.md` — the full draft (English), following the section plan in
  `docs/superpowers/specs/2026-10-18-harness-ablation-research-design.md` §13.

The draft is framed as a general study of harness components (R = persistent
execution, S = recursive delegation, M = durable state). The concrete
implementation is named only in the artifact appendix, where reproducibility
requires it; the claims do not depend on the product identity.

## Status: DRAFT, not submission-ready

The numbers in the draft are from the completed 720-cell main matrix (three
families x 30 instances x 8 treatments) and, for contrast, the 120-cell pilot.
The non-guessable-threshold mechanism check, repeat runs, strong-model
replication, selector evaluation, and external benchmarks are still pending; the
draft marks those as pending rather than claiming them.

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

## Reproducing the pilot

```bash
python3 experiments/harness-ablation/runner/runner.py \
  --config experiments/harness-ablation/runner/config.pilot.json \
  --binary target/debug/prime-agent \
  --results /tmp/pilot --mode live
python3 experiments/harness-ablation/runner/analyze.py --results /tmp/pilot
```
