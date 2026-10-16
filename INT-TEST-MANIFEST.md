# INT-TEST-MANIFEST (sebastian/int-test-2)

Integration branch for local testing only. Never merged; no PR.

- Base: origin/main @ `a01c7923cfcb8d130b50d24332db1413b0e7479c` (main has since moved to `c3f4ac87b`; not re-merged).
- Merge method: `git merge --no-ff -S origin/<branch>` in the operator's order; conflicts resolved only when trivial (both-sides additions, or one-side-only changes), by 3-way reconstruction. Anything else: skipped.
- PR heads re-read with `gh pr view` before merging and again before pushing: none moved.

## PRs

| # | PR | Branch | Head merged | Status | Notes | Merge commit |
|---|---|---|---|---|---|---|
| 1 | #3640 | sebastian/persistent-bash | eb556bd0a | merged | clean | 683ba83dd |
| 2 | #3638 | sebastian/dock-expanded-view | f2105e8de | merged | clean | d6a73705e |
| 3 | #3639 | sebastian/agents-view-child-lines | 156f1255d | merged via #3641 | branch merged through #3641 (author resolution 279fab5b5 of the #3638 x #3639 clash) | — |
| 4 | #3641 | sebastian/persistent-counts | bee364d8d | merged | clean | 13d90670d |
| 5 | #3655 | sebastian/factory-spec-counts | 83647f359 | merged | conflict: chrome/tests.rs (took #3655 block; heartbeats line equal) | 305ee5ddc |
| 6 | #3653 | sebastian/empty-named-sessions | 3f807bdce | merged | clean | 614b34448 |
| 7 | #3654 | sebastian/scoped-empty-agents-view | 6ab3b3470 | merged | conflict: agents_view_forest/tests.rs (kept wrapper + new arg) | 9136bd03e |
| 8 | #3656 | sebastian/paginated-sessions | 9b2a9c724 | merged | conflict: supervisor/sessions.rs, agents_view.rs (orthogonal; `..` re-applied) | 7505faf6b |
| 9 | #3657 | sebastian/remove-session-archive | 8268a367a | merged | conflict: session_archive.rs modify/delete (kept deletion, pre-approved) | 7cdda5378 |
| 10 | #3659 | sebastian/orphan-shells-view | cd9aa7691 | merged | conflict: agents_view.rs, delete.rs, render.rs, agents_view_forest.rs, bash_view/tests.rs (parallel additions) | f4147ad8e |
| 11 | #3643 | sebastian/transcript-pump | 649abda7e | merged | clean | ac13b2daa |
| 12 | #3649 | sebastian/deflake-refinement-abort | 50cbb5bab | merged | clean | a4c627027 |
| 13 | #3628 | sebastian/default-model-private-race | c908a1809 | merged | conflict: session_ui/lifecycle.rs (same resolution as int-test ffef5be47; borderline) | 2373025d1 |
| 14 | #3631 | sebastian/factory-escalate-retry | c84284eec | merged | clean | 8b385c305 |
| 15 | #3632 | sebastian/daemon-dead-child-delete | 1157718c8 | merged | clean | ec91ad845 |
| 16 | #3633 | sebastian/kernel-venv-readonly | a592b5a52 | merged | clean | 7de370e2f |
| 17 | #3634 | sebastian/replay-persisted-rows | d7017639f | merged | conflict: 5 files vs main #3560 images (same resolution as int-test a96feb258) | b42945635 |
| 18 | #3604 | sebastian/at-completion-quality | 5a4baced4 | merged | clean | a947e03ab |
| 19 | #3610 | sebastian/pg-drop-allmessagestext | f91b28bf9 | merged | conflict: pa-daemon/README.md (two deletions) | aa896b88b |
| 20 | #3614 | sebastian/pg-sidecar-ownership | ea57c7386 | merged | clean | 18efc2110 |
| 21 | #3619 | sebastian/pg-ledger-view | 3dd3475c6 | SKIPPED | breaks with #3657 (deleted session_archive) — see below | — |
| 22 | #3622 | sebastian/pg-family-seed | c5fb14b79 | SKIPPED | contains #3619; same breakage — see below | — |
| 23 | #3616 | sebastian/mcp-preregistered-client | 0a5baed80 | merged | clean | d9085727a |
| 24 | #3404 | fix/darwin-stop-escalation-signals | 71dc614ed | merged | clean | 262c0a0fd |
| 25 | #3406 | sebastian/refine-unlocked-llm | 2a62cf400 | already in via #3634 | #3634 is based on it; merge was a no-op | — |
| 26 | #3407 | fix/graceful-shutdown-continuation | 230b06e75 | SKIPPED | logic/signature clash with #3404 — see below | — |
| 27 | #3409 | sebastian/compaction-watermarks | a55905705 | SKIPPED | logic conflicts in 6 files — see below | — |
| 28 | #3424 | sebastian/osc-7501-program-status | d2d1f5afd | SKIPPED | borderline placement vs #3643 — see below | — |
| 29 | #3523 | sebastian/bash-bounded-await | f3881d536 | SKIPPED | design clash with #3640 — see below | — |
| 30 | #3526 | sebastian/output-head-tail | 38da5339d | merged | clean | 68ba8b6fa |
| 31 | #3529 | sebastian/reject-unusable-summaries | cb708c7df | merged | clean | 1ef9e256f |

## Skipped PRs — what breaks

- **#3619 pg-ledger-view, #3622 pg-family-seed**: they merge cleanly, but they do not compile after #3657 (remove-session-archive), which deletes `crate::session_archive`.
  - #3619 uses it in production: `crates/pa-daemon/src/rlm_ledger/replay.rs` (2028dc89e) treats a recorded endpoint whose file sits in `session_archive::archive_dir(..)` as not live.
  - #3619 also uses it in tests: `rlm_ledger/tests.rs` (2028dc89e; 7014e56b6 uses `archive_dir` / `restore_session`).
  - #3622 adds one more restore test (2c7d71114).
  - Making them compile means deciding what the ledger does once the archive is gone, so both were dropped. The other option is to drop #3657 instead.
- **#3407 graceful-shutdown-continuation**: logic and signature clash with #3404.
  - `boot_reap.rs` `stop_target_within`: #3404 adds an `escalate()` ladder plus a kill(2) carrier; #3407 adds a `wait_for_exit` watch.
  - `supervisor/worker_lifecycle.rs` `retire_worker_after_stop`: #3404 makes it return `bool`; #3407 adds a `keep_interrupted_work` parameter.
- **#3409 compaction-watermarks**: logic conflicts in 6 files (`compaction_threshold`, the compaction attempt loop, and others). gh already reports it CONFLICTING against main.
- **#3424 osc-7501-program-status** (borderline): its `view.retry = None` lands inside the block that #3643 moved into the shared `replace_transcript(..)`. Placing it is a behavior choice. gh: CONFLICTING against main.
- **#3523 bash-bounded-await**: design clash with #3640. Both extend `rlm.bash()`: `persistent=` vs `yield_after=`. `PersistentBashHandle` does not carry the bounded-await state.

## Conflict summary (details: merge-notes.md in the crew dir)

- #3655: `pa-tui chrome/tests.rs` expected dock rows. Took #3655's block; the heartbeats line was equal on both sides.
- #3654: `agents_view_forest/tests.rs` `rows_for`. Kept the `rows_with_activity` wrapper and applied the new `None` argument.
- #3656:
  - `supervisor/sessions.rs`: kept the shells map and the paging cursor/limit; tuple `(page, file_total, persistent_shells)`; `saved_session_rows(..)` plus `nextCursor`/`remaining`.
  - `agents_view.rs`: `SavedCatalog` plus the `session_activity` field. Re-applied #3641's `..` to #3656's rewritten `AgentsViewLink` pattern.
- #3657: kept the deletion of `session_archive.rs` (#3640 had only added one test there). Pre-approved.
- #3659: per-session activity lines (#3641) and machine-wide persistent-shell rows (#3659) kept side by side; match alternatives unioned.
- #3628: `session_ui/lifecycle.rs`. The fallback warning row goes after `replace_transcript(..)`. Same resolution as int-test merge ffef5be47.
- #3634: 5 files against main's #3560 `images` argument. Same resolution as int-test merge a96feb258.
- #3610: `pa-daemon/README.md`. Applied both deletions.

## Fixup commits (non-textual breakage between merged PRs; one per issue)

- F1 `db1189190` (#3654 x #3641/#3655): thread #3654's new `scope_root_session` argument (`None`) into two agents_view_forest tests from #3641 (09f3c0b00) / #3655 (d6fd0412d) that still used the 2-arg `reconcile_unified_sessions`.
- F2 `69d5a82dd` (#3610 x #3640/#3655): pass `&HashMap::new()` (persistent-shells map) to `saved_session_row` in #3610's golden test `saved_session_rows_match_the_catalog_row_golden` (556cb0683); #3640/#3655 added the second argument.
- F3 `f6a5c1815` (#3634 x main #3560): pass `&[]` images to `execute_session_command` / `execute_prompt_session_command` in #3634's new pa-cli test `print_boundary/tests/refinement_nonblocking.rs`; main's #3560 added the argument (same fix crew int-test made inside merge a96feb258).

## Checks

- Prime sandbox (rust:1-bookworm VM) on `git archive` of `f6a5c1815`: `cargo +1.98.1 fmt --all --check` passes; `cargo +1.98.1 check --workspace --all-targets` passes.
- The commit after this manifest changes `.github/workflows/continuous.yml`: it adds a push trigger for this branch and `workflow_dispatch`, and makes the concurrency group per ref. The workflow publishes nothing; it only uploads run artifacts.
