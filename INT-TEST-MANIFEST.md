# INT-TEST-MANIFEST — sebastian/int-test-7

Integration build of all open snimu PRs that merge trivially, for the int-test install. Not for merge.

- Base: origin/main @ `7cbffa77b38facd7c87277fa3c82bcadbe816880`
- Merge method: `git merge --no-ff -S <head>` in plan order (int-test-6's 16, then #3674 last); conflicts resolved only when trivial, or by the explicit #3643-vs-#3674 rule (take #3674's compaction transcript path, keep #3643's queued-work pause). Anything else is skipped.
- Compile check (Prime sandbox, cargo +1.98.1): `fmt --all --check` and `check --workspace --all-targets` pass on the last fixup commit's tree.
- Result: 18 of 38 PRs in (17 merge commits + #3639 via #3641), 20 skipped/dropped.
- Changes since int-test-6 (sebastian/int-test-6 @ fe019ebde), all fast-forward:
  - #3640 63c69d6b9 -> 9e6dcead5 (test reap wait; a watched persistent shell's leader exit kills the members it leaves behind);
  - #3641 00a796c84 -> e68aae33f (an inactive session's shells line follows the daemon's live persistent shells; adds its own `list_persistent_shells`); still carries #3639 14df6a9d8;
  - #3655 ad6061568 -> b657bc532 (merges #3641 e68aae33f);
  - NEW #3674 sebastian/transcript-window @ 77da6466c (named cad4cb73a at planning; +3 FF commits 7a0bfba2e, 239093db0, 77da6466c), merged last.
  The other skip reasons carry over (same base, same skip heads).

| # | PR | Branch | Head | Status | Reason | Merge commit |
|---|---|---|---|---|---|---|
| 1 | #3640 | sebastian/persistent-bash | 9e6dcead5 | merged | CI 9e6dcead5: all green (incl. shard 1/8, Bugbot, Macroscope) | fea056397 |
| 2 | #3638 | sebastian/dock-expanded-view | 72daa0c3f | merged |  | 104c6b382 |
| 3 | #3639 | sebastian/agents-view-child-lines | 14df6a9d8 | merged via #3641 | #3641's branch (e68aae33f) carries #3639 14df6a9d8 (unchanged). CI: pinned-TS dock_events parity red since 232c11fd0 (stale plural regex vs singular wording); accepted, author's to fix | — |
| 4 | #3641 | sebastian/persistent-counts | e68aae33f | merged | head e68aae33f (inactive session's shells line follows the daemon's live persistent shells). CI: shard 1/8 = drag_select_perf timing flake (642ms) + known parity set; 26/27 units green; accepted (gates/ci-3641-triage.md) | cd34bb761 |
| 5 | #3655 | sebastian/factory-spec-counts | b657bc532 | merged | merges #3641 e68aae33f. CI b657bc532 (run 39247929399): 27/27 shard-1 units green; parity step only (dock_events accepted + main's draft_disconnect/goal_backoff); Macroscope pass, Bugbot skipping | 5622e4142 |
| 6 | #3659 | sebastian/orphan-shells-view | 7989c65f2 | merged | FRESH conflicts vs #3641 e68aae33f (both add list_persistent_shells): daemon/types = #3659's superset (shell_rows + tail/kill; #3641's live_shell_rows dropped), agents_view.rs = union; manager-approved. Author-side: agents view polls list_persistent_shells twice per cycle | 3dde0f78b |
| 7 | #3656 | sebastian/paginated-sessions | be0757a0e | merged |  | d246edc6b |
| 8 | #3657 | sebastian/remove-session-archive | 617da0253 | merged |  | 23dc95303 |
| 9 | #3643 | sebastian/transcript-pump | 06b1ccbdb | merged | #3643's TUI compaction_end transcript replacement superseded by #3674 (merge + F3); queued-work pause kept. #3643 daemon still attaches compaction_end.result.transcript (inert for the #3674 TUI; extra payload per compaction); for the author to drop on rebase. Expected red: queued_compaction_dup_headless::legacy_compaction_results_refresh_the_transcript | 2619f8092 |
| 10 | #3628 | sebastian/default-model-private-race | 944d338f3 | merged |  | 77c1730fc |
| 11 | #3633 | sebastian/kernel-venv-readonly | 487db6f1f | merged |  | aff174693 |
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
| 24 | #3404 | fix/darwin-stop-escalation-signals | 71dc614ed | merged |  | 08bc86703 |
| 25 | #3406 | sebastian/refine-unlocked-llm | 2a62cf400 | skipped | base of #3634; same semantic conflicts (worker.rs vs #3643, boundary.rs vs #3628) | — |
| 26 | #3407 | fix/graceful-shutdown-continuation | 230b06e75 | skipped | logic/signature clash with #3404 (retire_worker_after_stop: bool return vs keep_interrupted_work param; boot_reap.rs) | — |
| 27 | #3409 | sebastian/compaction-watermarks | a55905705 | skipped | logic conflicts in 4 compaction files vs main (threshold/settings rework, adopt_built_session) | — |
| 28 | #3424 | sebastian/osc-7501-program-status | d2d1f5afd | skipped | borderline: `view.retry = None` inside #3643's shared replace_transcript or only in rebuild_view is a behavior choice | — |
| 29 | #3523 | sebastian/bash-bounded-await | 81fc88c03 | skipped | design clash with #3640 in rlm/bash.py (bash(yield_after=) bounded await vs bash(persistent=) PersistentBashHandle) | — |
| 30 | #3526 | sebastian/output-head-tail | 38da5339d | merged |  | 207b680eb |
| 31 | #3529 | sebastian/reject-unusable-summaries | cb708c7df | merged |  | 1b0556f4e |
| 32 | #3554 | sebastian/remove-parity-stubs | 678331876 | skipped | deletes parity scripts + ci.yml step that main has modified since (modify/delete) | — |
| 33 | #3576 | sebastian/roster-observe-fidelity | b8f4ae3e6 | merged |  | ae15b1f0d |
| 34 | #3580 | sebastian/print-engine-wiring | cd920835d | merged |  | 859c76217 |
| 35 | #3610 | sebastian/pg-drop-allmessagestext | ebbdca8b7 | merged |  | 10aa0222b |
| 36 | #3622 | sebastian/pg-family-seed | 71ed59fb4 | DROPPED | dropped: its test uses crate::session_archive (archive_dir/restore_session), deleted by #3657 (merges cleanly; compile failure certain) | — |
| 37 | #3630 | sebastian/queued-message-dup | 419555612 | skipped | superseded by #3643 (#3643 body: "#3630 closes unmerged once this lands") | — |
| 38 | #3674 | sebastian/transcript-window | 77da6466c | merged | NEW, merged last; parent named cad4cb73a, taken at current FF head 77da6466c (+7a0bfba2e, 239093db0, 77da6466c). Conflicts with #3643 resolved #3674-side (rule) + F3. CI all green | 9561df4e2 |

## Conflict summary (per-hunk details in the crew's merge-notes.md)
- #3659 (NEW vs #3641 e68aae33f): both add the `list_persistent_shells` daemon command. Took #3659's superset everywhere in the daemon/types (`shell_rows` with running/unverified/finished, tail/kill commands; #3641's live-only `live_shell_rows` dropped, it had no other caller). #3641's agents-view counts read only `status == "running"` rows, so its counts are unchanged. agents_view.rs = #3659's persistent-shells fields/arms + #3641's `ActivityCounts`/`cycle`. Each command/capability entry appears exactly once. Manager-approved. Not fixed (author's): the agents view polls `list_persistent_shells` twice per cycle.
- #3659: bash_view.rs / bash_view/tests.rs — same as int-test-6.
- #3656: supervisor/sessions.rs — same as int-test-6. agents_view.rs — `SavedCatalog` plus #3641's `session_activity` (now `ActivityCounts`) / `activity_fetch` fields.
- #3628: session_ui/lifecycle.rs — warning row after #3643's `replace_transcript(..)` (same as earlier builds).
- #3580: pa-agent/src/agent.rs — both lines kept. #3610: pa-daemon/README.md — both deletions.
- #3674 (NEW, vs #3643/#3641/#3659/#3628): run.rs, session_ui.rs, apply.rs, lifecycle.rs init = #3674's side (drops #3643's `compaction_refresh_pending`, generalized compaction refetch and the CompactionEnd replace-from-`result.transcript` arm); run.rs open task = #3674's `page_tx` + #3659's `AgentsView { .. }` route; lifecycle.rs tests = #3674 deletes the backfill-epoch test, the other PRs' tests (#3641 rebind, #3659 shell open, #3628 model fallback) and their mock helper stay; snapshot.rs = #3628's `model_fallback_message` + #3674's `history_before_entry_id`.
- #3643 x #3674 leftovers: #3643 daemon still attaches `compaction_end.result.transcript` (inert for the #3674 TUI; extra payload per compaction); for the author to drop on rebase. Expected red: `queued_compaction_dup_headless::legacy_compaction_results_refresh_the_transcript` (#3674 never refetches on compaction).

## Fixup commits
- `0e1f0bd12` F3 (#3643 x #3674, rule): CompactionEnd keeps `custom_instructions` (decoder.rs == main/#3674; #3674's summary row uses it), `replace_transcript` no longer clears the dropped `compaction_refresh_pending`, run loop drops #3643's unused `was_compacting`/`compaction_generation`. The summary row is pushed once at compaction_end; nothing refetches.
- `ff521947d` F4 (#3641/#3659 tests x #3674): the session_ui test `mock_session` helper takes #3674's `TranscriptPageNote` sender.
- `62a922eb8` (#3610 x #3640/#3655): pass the persistent-shells and factory-spec maps to `saved_session_row` in #3610's golden test.
- `69167ab24` (#3656 x #3655): `limit: None, cursor: None` in #3655's one-harness-read listing test.
