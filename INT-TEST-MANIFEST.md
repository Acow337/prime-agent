# INT-TEST-MANIFEST — sebastian/int-test-6

Integration build of all open snimu PRs that merge trivially, for the int-test install. Not for merge.

- Base: origin/main @ `7cbffa77b38facd7c87277fa3c82bcadbe816880`
- Merge method: `git merge --no-ff -S <head>` in plan order; conflicts resolved only when trivial (both sides pure additions at one anchor, or one side changes lines the other never touches). Anything else is skipped.
- Compile check (Prime sandbox, cargo +1.98.1): `fmt --all --check` and `check --workspace --all-targets` pass on the last fixup commit's tree.
- Result: 17 of 37 PRs in (16 merge commits + #3639 via #3641), 20 skipped/dropped.
- Changes since int-test-5 (sebastian/int-test-5 @ 9b6a86eac): five head moves, all fast-forward:
  - #3640 f793232b8 -> 63c69d6b9 (drop leftover persistent-shell tests and internal prompt detail);
  - #3639 232c11fd0 -> 14df6a9d8, merged via #3641 (a stopped session's shells and factory runs leave the chat dock);
  - #3641 26a40973e -> 00a796c84 (merges #3639 14df6a9d8 + #3640 63c69d6b9);
  - #3655 1b215d89f -> ad6061568 (merges #3641 00a796c84; one harness read per session per saved-session listing);
  - #3659 36a973de7 -> 7989c65f2 (Enter on a shell row opens its session's Bash panel on it; an orphan opens the standalone view 'Session deleted').
  The skip reasons carry over from int-test-4's trial-merge re-check (same base, same skip heads).

| # | PR | Branch | Head | Status | Reason | Merge commit |
|---|---|---|---|---|---|---|
| 1 | #3640 | sebastian/persistent-bash | 63c69d6b9 | merged | pinned at the gated head; newer head 8a4d85956 (pushed after the trial passed: test-only, test_bash.py waits for the killed persistent group to be reaped) NOT included | 32c467ef4 |
| 2 | #3638 | sebastian/dock-expanded-view | 72daa0c3f | merged |  | 6dc70b28a |
| 3 | #3639 | sebastian/agents-view-child-lines | 14df6a9d8 | merged via #3641 | #3641's branch (00a796c84) carries #3639 14df6a9d8 with the author's resolution of the standalone panels.rs/bash_view conflict with #3638. CI: pinned-TS dock_events parity red since 232c11fd0 (stale plural regex vs singular wording); accepted, author's to fix | — |
| 4 | #3641 | sebastian/persistent-counts | 00a796c84 | merged | CI: windows toolchain-install hang + drag_select_perf timing flake (598ms); covered by #3655 cb390aca2 green run (superset) | 2f9c0ce66 |
| 5 | #3655 | sebastian/factory-spec-counts | ad6061568 | merged | head moved mid-run cb390aca2 -> ad6061568 (perf: one harness read per session per listing); gated on ad6061568's run 39219542691 (parity step only, 27/27 units) | cfb7bb728 |
| 6 | #3659 | sebastian/orphan-shells-view | 7989c65f2 | merged |  | 729c52340 |
| 7 | #3656 | sebastian/paginated-sessions | be0757a0e | merged |  | 1b5e4966c |
| 8 | #3657 | sebastian/remove-session-archive | 617da0253 | merged |  | 51fa90e64 |
| 9 | #3643 | sebastian/transcript-pump | 06b1ccbdb | merged |  | 686d0fbf8 |
| 10 | #3628 | sebastian/default-model-private-race | 944d338f3 | merged |  | 81f806ea3 |
| 11 | #3633 | sebastian/kernel-venv-readonly | 487db6f1f | merged |  | fc1655260 |
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
| 24 | #3404 | fix/darwin-stop-escalation-signals | 71dc614ed | merged |  | 49bba21eb |
| 25 | #3406 | sebastian/refine-unlocked-llm | 2a62cf400 | skipped | base of #3634; same semantic conflicts (worker.rs vs #3643, boundary.rs vs #3628) | — |
| 26 | #3407 | fix/graceful-shutdown-continuation | 230b06e75 | skipped | logic/signature clash with #3404 (retire_worker_after_stop: bool return vs keep_interrupted_work param; boot_reap.rs) | — |
| 27 | #3409 | sebastian/compaction-watermarks | a55905705 | skipped | logic conflicts in 4 compaction files vs main (threshold/settings rework, adopt_built_session) | — |
| 28 | #3424 | sebastian/osc-7501-program-status | d2d1f5afd | skipped | borderline: `view.retry = None` inside #3643's shared replace_transcript or only in rebuild_view is a behavior choice | — |
| 29 | #3523 | sebastian/bash-bounded-await | 81fc88c03 | skipped | design clash with #3640 in rlm/bash.py (bash(yield_after=) bounded await vs bash(persistent=) PersistentBashHandle) | — |
| 30 | #3526 | sebastian/output-head-tail | 38da5339d | merged |  | 8de06dfdb |
| 31 | #3529 | sebastian/reject-unusable-summaries | cb708c7df | merged |  | ab60b2ee8 |
| 32 | #3554 | sebastian/remove-parity-stubs | 678331876 | skipped | deletes parity scripts + ci.yml step that main has modified since (modify/delete) | — |
| 33 | #3576 | sebastian/roster-observe-fidelity | b8f4ae3e6 | merged |  | cf596aabc |
| 34 | #3580 | sebastian/print-engine-wiring | cd920835d | merged |  | 9b46c2476 |
| 35 | #3610 | sebastian/pg-drop-allmessagestext | ebbdca8b7 | merged |  | 5e5b1041c |
| 36 | #3622 | sebastian/pg-family-seed | 71ed59fb4 | DROPPED | dropped: its test uses crate::session_archive (archive_dir/restore_session), deleted by #3657 (merges cleanly; compile failure certain) | — |
| 37 | #3630 | sebastian/queued-message-dup | 419555612 | skipped | superseded by #3643 (#3643 body: "#3630 closes unmerged once this lands") | — |

## Conflict summary (per-hunk details in the crew's merge-notes.md)
- #3659: bash_view.rs — took #3659's `pub(crate) use format_duration` import (superset of #3641's change). bash_view/tests.rs — #3641's dock-items test, #3659's two new view tests, then #3641's rewritten `running_shell_counts_skip_finished_shells` (it replaces #3639's older test, which both sides carried). The agents_view/delete/render files merge cleanly now that #3659 carries #3639 itself.
- #3656: supervisor/sessions.rs — kept the shells map, #3655's factory-spec map and the paging cursor/limit (tuple `(page, file_total, persistent_shells, factory_specs)`; 3-arg terminal rows plus `remaining`/`nextCursor`). agents_view.rs — `SavedCatalog` plus #3641's `session_activity`/`activity_fetch` fields (same as int-test-5).
- #3628: session_ui/lifecycle.rs — warning row after #3643's `replace_transcript(..)` (same as earlier builds).
- #3580: pa-agent/src/agent.rs `process_events` — both sides added one line at the same anchor; kept both.
- #3610: pa-daemon/README.md — applied both sides' deletions.
- #3657 merges cleanly now (#3640 63c69d6b9 no longer touches session_archive.rs).

## Fixup commits
- `e644a92b2` (#3610 x #3640/#3655): pass the persistent-shells and factory-spec maps to `saved_session_row` in #3610's golden test.
- `618be1bee` (#3656 x #3655): `limit: None, cursor: None` in #3655's one-harness-read listing test.
