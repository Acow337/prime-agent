# INT-TEST-MANIFEST

Integration build `int-test` — branch `sebastian/int-test`. Never merged; no PR.

Base: `origin/main` @ `a01c7923cfcb8d130b50d24332db1413b0e7479c` (fix: honor declared reasoning parameters on private Prime Inference routes (#3621)).

## Merged branch heads (in order, `git merge --no-ff`)

| # | Branch | PR | Head merged | Merge commit |
|---|--------|----|-------------|--------------|
| 1 | `sebastian/persistent-counts` | #3641 (carries #3640 #3638 #3639) | `5584e0eb1d0a571b065328214e919b2de63a7929` | `f4c9ac1a340d` |
| 2 | `sebastian/pg-ledger-view` | #3619 | `3dd3475c68345ca3cf8f8685ea34b83058f7691e` | `71ed7108fe91` |
| 3 | `sebastian/pg-family-seed` | #3622 | `c5fb14b79d437ce307277a2fb794c522f2184d2a` | `0c9c4c46e148` |
| 4 | `sebastian/pg-drop-allmessagestext` | #3610 | `f91b28bf9f604c6621f06eb46bc96c89da143f03` | `f75ed06af2b2` |
| 5 | `sebastian/pg-sidecar-ownership` | #3614 | `ea57c7386988c626e36375b6b8aecb12228c6ed6` | `1eb83e290a3c` |
| 6 | `sebastian/factory-escalate-retry` | #3631 | `c84284eecd8488d785f00d414a50b94ae772251d` | `0fc1f78987b5` |
| 7 | `sebastian/daemon-dead-child-delete` | #3632 | `1157718c8626074f30b16b1cb2d0706d8f08cfc2` | `aaf476700f09` |
| 8 | `sebastian/default-model-private-race` | #3628 | `c908a18092f398e68bf50ce5db7fdf1e658295cd` | `a90bd37df483` |
| 9 | `sebastian/kernel-venv-readonly` | #3633 | `a592b5a5237469ca61052ab1f339968b8535886c` | `fd7b4530e5e4` |
| 10 | `sebastian/replay-persisted-rows` | #3634 (carries #3406) | `d7017639fe62ae23f1ef8ffca0a9c4b60a3685a6` | `a96feb258ac2` |
| 11 | `sebastian/transcript-pump` | #3643 (replaces #3630) | `6a83da8ae391e2ca0857e9961cc7e14df282db0f` | `ffef5be47ca8` |
| 12 | `sebastian/deflake-refinement-abort` | #3649 | `50cbb5bab467f785bb5b7c761cdd910d4a03bc79` | `713be82f12bd` |

Heads re-checked with `git ls-remote origin` after the last merge: none moved.

## Already in base (squash-merged into main, branches deleted) — not merged

| PR | Branch | Main commit |
|----|--------|-------------|
| #3629 | factory-authoring-notes | `f2425dc1b` |
| #3627 | factory-view-left-right | `06083be66` |
| #3636 | browse-user-only | `a3d3fac89` |
| #3601 | shutdown-scope | `3407a54d8` |

## Conflict summary

- #3622 pg-family-seed: `crates/pa-daemon/src/rlm_ledger/tests.rs` (both sides appended tests; kept all). Semantic: #3619 made `resolve_live_edges` return `Result`; #3622's `family_topology` now uses `?`.
- #3634 replay-persisted-rows: stale against main's #3560 (session-command `images` argument). `pa-core session_commands.rs`, `pa-daemon agent_engine.rs`, `compact_autorefine.rs`, `rpc/prompt_commands.rs`, `session_commands.rs` — took #3634's background-refinement shape plus #3560's `images` threading; two #3634 test call sites in `pa-cli print_boundary/tests/refinement_nonblocking.rs` gained `&[]` images.
- #3643 transcript-pump: `crates/pa-tui/src/session_ui/lifecycle.rs` — `replace_transcript(..)` (#3643) followed by #3628's model-fallback warning row.
- Fix commit `24d25cbbb`: `Vec::<usize>::new()` in two #3622 topology test asserts (`usize: PartialEq<_>` ambiguity with serde_json).

## Extra commits

- `.github/workflows/continuous.yml`: also runs on push to `sebastian/int-test` and on `workflow_dispatch` so the macOS artifact builds, and the concurrency group is per-ref (`continuous-${{ github.ref_name }}`, still `continuous-main` on main) so int-test runs never queue behind or cancel main's pending run. The workflow uploads run artifacts only; it publishes nothing (no tags, releases, or channel/R2 writes), so no job needed an extra `if:`.
