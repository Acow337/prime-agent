# INT-TEST-MANIFEST — sebastian/int-test-3

Integration build of all open snimu PRs that merge trivially, for the int-test install. Not for merge.

- Base: origin/main @ `7cbffa77b38facd7c87277fa3c82bcadbe816880`
- Merge method: `git merge --no-ff -S origin/<branch>` in plan order; conflicts resolved only when trivial (both sides pure additions at one anchor, or one side changes lines the other never touches). Anything else is skipped.
- Compile check (Prime sandbox, cargo +1.98.1): `fmt --all --check` and `check --workspace --all-targets` pass on the fixup commit's tree.
- Result: 17 of 37 PRs in (16 merge commits + #3639 via #3641), 20 skipped/dropped.

| # | PR | Branch | Head | Status | Reason | Merge commit |
|---|---|---|---|---|---|---|
| 1 | #3640 | sebastian/persistent-bash | f793232b8 | merged |  | 68ebba6b4 |
| 2 | #3638 | sebastian/dock-expanded-view | 72daa0c3f | merged |  | f7240499b |
| 3 | #3639 | sebastian/agents-view-child-lines | a9a11e794 | merged via #3641 | #3641's branch carries #3639 with the author's resolution of the standalone panels.rs/bash_view conflict with #3638 | — |
| 4 | #3641 | sebastian/persistent-counts | 742c06c8b | merged |  | ef0135635 |
| 5 | #3655 | sebastian/factory-spec-counts | fdc49790c | merged |  | 7dea62020 |
| 6 | #3659 | sebastian/orphan-shells-view | 0a1798a9d | merged |  | 384f967d1 |
| 7 | #3656 | sebastian/paginated-sessions | be0757a0e | merged |  | eb324e990 |
| 8 | #3657 | sebastian/remove-session-archive | 617da0253 | merged |  | 3ba5c68a7 |
| 9 | #3643 | sebastian/transcript-pump | 06b1ccbdb | merged |  | 5cced1bde |
| 10 | #3628 | sebastian/default-model-private-race | 18baff04d | merged |  | 3500f0292 |
| 11 | #3633 | sebastian/kernel-venv-readonly | 487db6f1f | merged |  | 2335a30b2 |
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
| 24 | #3404 | fix/darwin-stop-escalation-signals | 71dc614ed | merged |  | f4faa92c7 |
| 25 | #3406 | sebastian/refine-unlocked-llm | 2a62cf400 | skipped | base of #3634; same semantic conflicts (worker.rs vs #3643, boundary.rs vs #3628) | — |
| 26 | #3407 | fix/graceful-shutdown-continuation | 230b06e75 | skipped | logic/signature clash with #3404 (retire_worker_after_stop: bool return vs keep_interrupted_work param; boot_reap.rs) | — |
| 27 | #3409 | sebastian/compaction-watermarks | a55905705 | skipped | logic conflicts in 4 compaction files vs main (threshold/settings rework, adopt_built_session) | — |
| 28 | #3424 | sebastian/osc-7501-program-status | d2d1f5afd | skipped | borderline: `view.retry = None` inside #3643's shared replace_transcript or only in rebuild_view is a behavior choice | — |
| 29 | #3523 | sebastian/bash-bounded-await | 81fc88c03 | skipped | design clash with #3640 in rlm/bash.py (bash(yield_after=) bounded await vs bash(persistent=) PersistentBashHandle) | — |
| 30 | #3526 | sebastian/output-head-tail | 38da5339d | merged |  | 41f64b9b7 |
| 31 | #3529 | sebastian/reject-unusable-summaries | cb708c7df | merged |  | 30b272495 |
| 32 | #3554 | sebastian/remove-parity-stubs | 678331876 | skipped | deletes parity scripts + ci.yml step that main has modified since (modify/delete) | — |
| 33 | #3576 | sebastian/roster-observe-fidelity | b8f4ae3e6 | merged |  | 8098e4bf7 |
| 34 | #3580 | sebastian/print-engine-wiring | cd920835d | merged |  | 60c5fe2a5 |
| 35 | #3610 | sebastian/pg-drop-allmessagestext | ebbdca8b7 | merged |  | 5398854e1 |
| 36 | #3622 | sebastian/pg-family-seed | 71ed59fb4 | DROPPED | dropped after compile check: its test uses crate::session_archive (archive_dir/restore_session), deleted by #3657 | — |
| 37 | #3630 | sebastian/queued-message-dup | 419555612 | skipped | superseded by #3643 (#3643 body: "#3630 closes unmerged once this lands") | — |

## Conflict summary (per-hunk details in the crew's merge-notes.md)
- #3659: agents_view.rs / render.rs / delete.rs / agents_view_forest.rs / bash_view(.rs, /tests.rs) — #3641's per-session activity lines and #3659's machine-wide persistent-shell rows are parallel additions; kept both (two separate `select!` arms; match alternatives unioned). bash_view.rs import: took #3659's `pub(crate) use` (superset of #3641's change).
- #3656: supervisor/sessions.rs — kept the shells map and the paging cursor/limit (tuple `(page, file_total, persistent_shells)`). agents_view.rs — `SavedCatalog` plus #3641's appended `session_activity`/`activity_fetch` fields; `..` re-applied to the rewritten `AgentsViewLink { .. }` pattern.
- #3657: modify/delete of session_archive.rs — kept the deletion (#3640 had only added one test there).
- #3628: session_ui/lifecycle.rs — warning row after #3643's `replace_transcript(..)` (same resolution as the int-test and int-test-2 builds).
- #3580: pa-agent/src/agent.rs `process_events` — both sides added one line at the same anchor; kept both.
- #3610: pa-daemon/README.md — applied both sides' deletions.
- #3643's head moved to 06b1ccbdb (rustfmt only) during the build; that head is merged.

## Fixup commits
- `4082663e3` (#3610 x #3640/#3655): pass the persistent-shells map to `saved_session_row` in #3610's golden test.
