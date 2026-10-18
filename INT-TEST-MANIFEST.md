# INT-TEST-MANIFEST — sebastian/int-test-5

Integration build of all open snimu PRs that merge trivially, for the int-test install. Not for merge.

- Base: origin/main @ `7cbffa77b38facd7c87277fa3c82bcadbe816880`
- Merge method: `git merge --no-ff -S origin/<branch>` in plan order; conflicts resolved only when trivial (both sides pure additions at one anchor, or one side changes lines the other never touches). Anything else is skipped.
- Compile check (Prime sandbox, cargo +1.98.1): `fmt --all --check` and `check --workspace --all-targets` pass on the fixup commit's tree.
- Result: 17 of 37 PRs in (16 merge commits + #3639 via #3641), 20 skipped/dropped.
- Changes since int-test-4 (sebastian/int-test-4 @ 9dbcba32f): only #3659's head moved, c538f387a -> 36a973de7 (shell rows render as session-table rows; the block lists only live shells; Ctrl+X on a shell row only stops it). The skip reasons carry over from int-test-4's trial-merge re-check (same base, same skip heads).

| # | PR | Branch | Head | Status | Reason | Merge commit |
|---|---|---|---|---|---|---|
| 1 | #3640 | sebastian/persistent-bash | f793232b8 | merged |  | c9b7f7eab |
| 2 | #3638 | sebastian/dock-expanded-view | 72daa0c3f | merged |  | 21344fc54 |
| 3 | #3639 | sebastian/agents-view-child-lines | 232c11fd0 | merged via #3641 | #3641's branch (26a40973e) carries #3639 232c11fd0 with the author's resolution of the standalone panels.rs/bash_view conflict with #3638 | — |
| 4 | #3641 | sebastian/persistent-counts | 26a40973e | merged |  | 24a3822d0 |
| 5 | #3655 | sebastian/factory-spec-counts | 1b215d89f | merged |  | 0e6c6a4f3 |
| 6 | #3659 | sebastian/orphan-shells-view | 36a973de7 | merged |  | e0b119313 |
| 7 | #3656 | sebastian/paginated-sessions | be0757a0e | merged |  | 5450de1ff |
| 8 | #3657 | sebastian/remove-session-archive | 617da0253 | merged |  | 3ea2b1578 |
| 9 | #3643 | sebastian/transcript-pump | 06b1ccbdb | merged |  | 1d0717aca |
| 10 | #3628 | sebastian/default-model-private-race | 944d338f3 | merged |  | 31dc6e5c8 |
| 11 | #3633 | sebastian/kernel-venv-readonly | 487db6f1f | merged |  | 65d25949a |
| 12 | #3634 | sebastian/replay-persisted-rows | d7017639f | skipped | semantic conflicts: worker.rs emit_refinement_row vs #3643 (broadcast_custom_row_locked); boundary.rs: deletes the refinement block that #3628 extends | — |
| 13 | #1896 | snimu/empty-turn-retry | cb37b79e1 | skipped | TS-era: modify/delete conflicts against the removed TS tree (Rust port #2524) | — |
| 14 | #2042 | feat/catalog-generation-invariants | 8d4cad029 | skipped | TS-era: modify/delete conflicts against the removed TS tree (Rust port #2524) | — |
| 15 | #2099 | fix/agents-view-rename-freeze | 8771d5d14 | skipped | TS-era: modify/delete conflicts against the removed TS tree (Rust port #2524) | — |
| 16 | #2150 | res-1333-broadcast-removal | e92e5271a | skipped | TS-era: modify/delete conflicts against the removed TS tree (Rust port #2524) | — |
| 17 | #2489 | snimu/fix-dup-providers | 89e7be93e | skipped | TS-era: modify/delete conflicts against the removed TS tree (Rust port #2524) | — |
| 18 | #2490 | snimu/fix-layering | e74595be1 | skipped | TS-era: modify/delete conflicts against the removed TS tree (Rust port #2524) | — |
| 19 | #2495 | snimu/fix-test-gate | 4bd8ddd42 | skipped | TS-era: modify/delete conflicts against the removed TS tree (Rust port #2524) | — |
| 20 | #2503 | snimu/crew-pins-ts | 74a947e98 | skipped | TS-era: modify/delete conflicts against the removed TS tree (Rust port #2524) | — |
| 21 | #2522 | snimu/crew-spawn-cwd | da0e27805 | skipped | TS-era: modify/delete conflicts against the removed TS tree (Rust port #2524) | — |
| 22 | #2526 | snimu/crew-agents-view-plus | d35993311 | skipped | TS-era: modify/delete conflicts against the removed TS tree (Rust port #2524) | — |
| 23 | #2528 | snimu/crew-cwd-command | 7892dc318 | skipped | TS-era: modify/delete conflicts against the removed TS tree (Rust port #2524) | — |
| 24 | #3404 | fix/darwin-stop-escalation-signals | 71dc614ed | merged |  | 859c44e7a |
| 25 | #3406 | sebastian/refine-unlocked-llm | 2a62cf400 | skipped | base of #3634; same semantic conflicts (worker.rs vs #3643, boundary.rs vs #3628) | — |
| 26 | #3407 | fix/graceful-shutdown-continuation | 230b06e75 | skipped | logic/signature clash with #3404 (retire_worker_after_stop: bool return vs keep_interrupted_work param; boot_reap.rs) | — |
| 27 | #3409 | sebastian/compaction-watermarks | a55905705 | skipped | logic conflicts in 4 compaction files vs main (threshold/settings rework, adopt_built_session) | — |
| 28 | #3424 | sebastian/osc-7501-program-status | d2d1f5afd | skipped | borderline: `view.retry = None` inside #3643's shared replace_transcript or only in rebuild_view is a behavior choice | — |
| 29 | #3523 | sebastian/bash-bounded-await | 81fc88c03 | skipped | design clash with #3640 in rlm/bash.py (bash(yield_after=) bounded await vs bash(persistent=) PersistentBashHandle) | — |
| 30 | #3526 | sebastian/output-head-tail | 38da5339d | merged |  | 7ce22eb6c |
| 31 | #3529 | sebastian/reject-unusable-summaries | cb708c7df | merged |  | 1669395ad |
| 32 | #3554 | sebastian/remove-parity-stubs | 678331876 | skipped | deletes parity scripts + ci.yml step that main has modified since (modify/delete) | — |
| 33 | #3576 | sebastian/roster-observe-fidelity | b8f4ae3e6 | merged |  | 7e78a76eb |
| 34 | #3580 | sebastian/print-engine-wiring | cd920835d | merged |  | 45c795940 |
| 35 | #3610 | sebastian/pg-drop-allmessagestext | ebbdca8b7 | merged |  | 1cb605595 |
| 36 | #3622 | sebastian/pg-family-seed | 71ed59fb4 | DROPPED | dropped: its test uses crate::session_archive (archive_dir/restore_session), deleted by #3657 (merges cleanly; compile failure certain) | — |
| 37 | #3630 | sebastian/queued-message-dup | 419555612 | skipped | superseded by #3643 (#3643 body: "#3630 closes unmerged once this lands") | — |

## Conflict summary (per-hunk details in the crew's merge-notes.md)
- #3659: agents_view.rs / agents_view_forest.rs — #3641's per-session activity lines and #3659's machine-wide persistent-shell rows are parallel additions; kept both (two separate `select!` arms; match alternatives unioned). bash_view.rs — took #3659's `pub(crate) use` import (superset of #3641's change); `BashActivity` keeps #3641's `persistent` and #3659's `session` fields (and both initializers). delete.rs — #3641's widened `Shells | FactoryRuns => None` arms, followed by #3659's `PersistentShell` arms (stop only). bash_view/tests.rs — both sides' fixture fields and appended tests kept.
- #3656: supervisor/sessions.rs — kept the shells map and the paging cursor/limit (tuple `(page, file_total, persistent_shells)`). agents_view.rs — `SavedCatalog` plus #3641's appended `session_activity`/`activity_fetch` fields; `..` re-applied to the rewritten `AgentsViewLink { .. }` pattern.
- #3657: modify/delete of session_archive.rs — kept the deletion (#3640 had only added one test there).
- #3628: session_ui/lifecycle.rs — warning row after #3643's `replace_transcript(..)` (same resolution as the int-test through int-test-4 builds).
- #3580: pa-agent/src/agent.rs `process_events` — both sides added one line at the same anchor; kept both.
- #3610: pa-daemon/README.md — applied both sides' deletions.

## Fixup commits
- `a506fcab0` (#3610 x #3640/#3655): pass the persistent-shells map to `saved_session_row` in #3610's golden test.
