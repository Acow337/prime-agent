# Paper draft

This directory holds the manuscript draft for the harness-ablation study.

- `paper.md` — the full draft (English), following the section plan in
  `docs/superpowers/specs/2026-10-18-harness-ablation-research-design.md` §13.

## Status: DRAFT, not submission-ready

The numbers currently in the draft are from:

1. the 120-cell pilot (all three families, 8 treatments, cheap model), and
2. the implementation evidence (treatment validity tests, verifier self-checks).

The 720-cell main experiment is still running; the draft marks those cells as
`[main run in progress]` and must be updated before any claim is made from it.
No strong-model replication, no external benchmark, and no selector evaluation
has been run yet.

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
