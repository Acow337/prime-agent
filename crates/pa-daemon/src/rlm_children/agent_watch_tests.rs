//! The `rlm.watch.agent` registry pins (the TS `agent-watch.test.ts`
//! regression intent): per-child baselines, range/status-only events, the
//! two caps, idempotent cancel with published events kept readable,
//! owner teardown, and one coalesced wake row per poll batch.
//! The settle watcher's pins live in `watch_tests.rs` (naming rule: the
//! subscription constants are `AGENT_WATCH_*`/`WATCH_SUB_*`, never the
//! bare `WATCH_*` settle names).

use super::*;
use crate::protocol::{response_failure, response_success};
use pa_types::platform::transport::bind_transport;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::sync::mpsc;

use super::agent_watch::{
    AgentWatchRegisterError, AgentWatchRegistry, AGENT_WATCH_MAX_ACTIVE, AGENT_WATCH_MAX_TOTAL,
};

/// The fake child state the scripted supervisor reports: the
/// `messageCount` per active session id (a test advances it) and the
/// `isStreaming` flag.
#[derive(Default)]
struct FakeWatchState {
    counts: std::sync::Mutex<HashMap<String, u64>>,
    streaming: std::sync::Mutex<HashMap<String, bool>>,
}

impl FakeWatchState {
    fn count(&self, active_session_id: &str, count: u64) {
        self.counts
            .lock()
            .unwrap()
            .insert(active_session_id.to_string(), count);
    }
}

/// A scripted JSONL supervisor for the watch tests: `create` mints a
/// fresh child identity per call, `get_state` reports the scripted
/// `messageCount` and streaming flag, and `follow_up` (the wake-row
/// delivery lane) is captured.
async fn spawn_watch_supervisor(
    socket: std::path::PathBuf,
    follow_up_tx: mpsc::UnboundedSender<Value>,
    state: Arc<FakeWatchState>,
) {
    let listener = bind_transport(&socket).await.unwrap();
    let created = Arc::new(AtomicU64::new(0));
    tokio::spawn(async move {
        loop {
            let Ok(stream) = listener.accept().await else {
                return;
            };
            let state = Arc::clone(&state);
            let follow_up_tx = follow_up_tx.clone();
            let created = Arc::clone(&created);
            tokio::spawn(async move {
                let (reader, mut writer) = stream.split();
                let mut reader = BufReader::new(reader);
                writer
                    .write_all(
                        b"{\"type\":\"daemon_hello\",\"protocol\":{\"name\":\"prime-agent.daemon\",\"version\":7}}\n",
                    )
                    .await
                    .unwrap();
                loop {
                    let mut line = String::new();
                    if reader.read_line(&mut line).await.unwrap() == 0 {
                        return;
                    }
                    let value: Value = serde_json::from_str(line.trim()).unwrap();
                    let id = value["id"].as_str().unwrap_or_default().to_string();
                    let command = value["command"].clone();
                    let command_type: &str = command["type"].as_str().unwrap_or_default();
                    let response = match command_type {
                        "create" => {
                            let n = created.fetch_add(1, Ordering::SeqCst) + 1;
                            response_success(
                                Some(&id),
                                command_type,
                                Some(json!({
                                    "activeSessionId": format!("watch-live-{n}"),
                                    "sessionId": format!("watch-file-{n}"),
                                    "sessionFile": format!("/tmp/pa-watch-fake-{n}.jsonl"),
                                    "sessionName": format!("watch-worker-{n}"),
                                })),
                            )
                        }
                        "get_state" => {
                            let active = command["activeSessionId"]
                                .as_str()
                                .unwrap_or_default()
                                .to_string();
                            let count = state
                                .counts
                                .lock()
                                .unwrap()
                                .get(&active)
                                .copied()
                                .unwrap_or(0);
                            let streaming = state
                                .streaming
                                .lock()
                                .unwrap()
                                .get(&active)
                                .copied()
                                .unwrap_or(false);
                            response_success(
                                Some(&id),
                                command_type,
                                Some(json!({
                                    "isStreaming": streaming,
                                    "hasRunningSubagents": false,
                                    "sessionActions": { "queuedCount": 0 },
                                    "messageCount": count,
                                })),
                            )
                        }
                        "follow_up" => {
                            let _ = follow_up_tx.send(command.clone());
                            response_success(
                                Some(&id),
                                command_type,
                                Some(json!({ "queued": true })),
                            )
                        }
                        "prompt" | "wait_for_idle" | "kill" => {
                            response_success(Some(&id), command_type, None)
                        }
                        "get_last_assistant_text" => response_success(
                            Some(&id),
                            command_type,
                            Some(json!({ "text": "the child final answer" })),
                        ),
                        other => response_failure(Some(&id), other, "unexpected command", None),
                    };
                    let mut line = serde_json::to_string(&response).unwrap();
                    line.push('\n');
                    if writer.write_all(line.as_bytes()).await.is_err() {
                        return;
                    }
                }
            });
        }
    });
}

/// Registry-backed sessions against the scripted supervisor.
async fn watch_sessions(
    follow_up_tx: mpsc::UnboundedSender<Value>,
    state: Arc<FakeWatchState>,
) -> SupervisorChildSessions {
    let socket = std::env::temp_dir().join(format!(
        "pa-aw-{}.sock",
        &uuid::Uuid::new_v4().simple().to_string()[..8]
    ));
    spawn_watch_supervisor(socket.clone(), follow_up_tx, state).await;
    let link = Arc::new(crate::supervisor_link::SupervisorLink::new(socket));
    let sessions = SupervisorChildSessions::new(
        link,
        std::env::temp_dir(),
        "parent-live".to_string(),
        std::sync::Arc::new(crate::model_allowlist::ModelRefusalTelemetry::new(
            std::env::temp_dir(),
            /*telemetry_disabled*/ true,
        )),
        String::new(),
    );
    sessions.set_identity(ParentIdentity {
        model: Some("mock/mock-1".to_string()),
        cwd: Some(std::env::temp_dir().to_string_lossy().to_string()),
        ..ParentIdentity::with_default_depth()
    });
    sessions
}

/// A test-knob registry session pair with a shortened poll cadence.
async fn quick_watch_sessions() -> (
    SupervisorChildSessions,
    mpsc::UnboundedReceiver<Value>,
    Arc<FakeWatchState>,
) {
    let (follow_up_tx, follow_up_rx) = mpsc::unbounded_channel();
    let state = Arc::new(FakeWatchState::default());
    let sessions = watch_sessions(follow_up_tx, Arc::clone(&state)).await;
    sessions
        .inner
        .agent_watch
        .lock()
        .await
        .set_poll_interval_ms(10);
    (sessions, follow_up_rx, state)
}

/// One pushed registry child (the `push_test_child` seam): no spawn, no
/// settle watcher — only the roster row a watch resolves against.
async fn push_child(sessions: &SupervisorChildSessions, child_id: &str, active: &str) {
    sessions
        .push_test_child(RlmChildIdentity {
            rlm_child_id: child_id.to_string(),
            active_session_id: active.to_string(),
            session_id: Some(format!("{active}-file")),
            session_name: format!("{child_id}-worker"),
        })
        .await;
}

/// TS `agent-watch.test.ts`, the core pin: the baseline emits nothing;
/// growth 3..7 emits one range event with the status transition; a
/// repeat tick with no growth emits nothing. **Mutation check:** delete
/// the quiet guard (`count == baseline && status == last`) and the
/// repeat tick emits, failing this test.
#[test]
fn agent_watch_registry_emits_ranges_and_status_only() {
    let mut registry = AgentWatchRegistry::default();
    registry
        .register("sub-1", "researcher", Some(3), 1_000)
        .expect("register the watched child");
    // The baseline tick: no growth, no status change — nothing.
    assert!(registry
        .apply_snapshot("watch-agent-sub-1", &Ok((3, "idle")), 1_500)
        .is_none());
    // Growth 3->7 with idle->running: one event with the range and the
    // transition.
    let event = registry
        .apply_snapshot("watch-agent-sub-1", &Ok((7, "running")), 2_000)
        .expect("growth emits one event");
    assert_eq!(event.kind, "watch.agent");
    assert_eq!(event.content.target, "sub-1");
    assert_eq!(event.content.start, 3);
    assert_eq!(event.content.end, 7);
    assert_eq!(
        event
            .content
            .status
            .as_ref()
            .map(|change| (change.from, change.to)),
        Some(("idle", "running"))
    );
    let event = registry.push_event(event);
    assert_eq!(event.seq, 1);
    // A repeat tick with no growth and no change: quiet.
    assert!(registry
        .apply_snapshot("watch-agent-sub-1", &Ok((7, "running")), 2_500)
        .is_none());
    // A status transition alone (no growth) still reports.
    let event = registry
        .apply_snapshot("watch-agent-sub-1", &Ok((7, "idle")), 3_000)
        .expect("a status transition emits");
    assert_eq!(event.content.start, 7);
    assert_eq!(event.content.end, 7);
    assert!(registry
        .apply_snapshot("watch-agent-sub-1", &Ok((7, "idle")), 3_500)
        .is_none());
    // Content is unreachable by construction: the snapshot is a count
    // and a label, never transcript text.
}

/// The two caps: the 65th active register refuses with `active`; the
/// 1025th monotonic registration refuses with `total`. Cancels never
/// refund the total (TS `totalRegistered`). **Mutation check:** remove
/// either bound and this test fails.
#[test]
fn agent_watch_enforces_active_and_total_limits() {
    let mut registry = AgentWatchRegistry::default();
    for index in 0..AGENT_WATCH_MAX_ACTIVE {
        let child = format!("sub-{index}");
        registry
            .register(&child, "worker", Some(0), 1_000 + index as u64)
            .unwrap_or_else(|_| panic!("register {index} must fit the active cap"));
    }
    let error = registry
        .register("sub-overflow", "worker", Some(0), 2_000)
        .expect_err("the 65th active register must refuse");
    assert_eq!(error, AgentWatchRegisterError::ActiveLimit);
    assert_eq!(
        error.message(),
        "Agent watch limit reached (64 active)",
        "the TS refusal text"
    );
    // Cancels free the ACTIVE slot but never refund the total: the
    // monotonic cap is reachable with zero live subscriptions.
    for index in 0..AGENT_WATCH_MAX_ACTIVE {
        assert!(registry.cancel(&format!("watch-agent-sub-{index}")));
    }
    let mut registered = AGENT_WATCH_MAX_ACTIVE as u64;
    while registered < AGENT_WATCH_MAX_TOTAL {
        let child = format!("sub-r{registered}");
        registry
            .register(&child, "worker", Some(0), 3_000)
            .unwrap_or_else(|_| panic!("the total cap must not fire at {registered}"));
        assert!(registry.cancel(&format!("watch-agent-{child}")));
        registered += 1;
    }
    let error = registry
        .register("sub-total-overflow", "worker", Some(0), 4_000)
        .expect_err("the 1025th registration must refuse");
    assert_eq!(error, AgentWatchRegisterError::TotalLimit);
    assert_eq!(error.message(), "Agent watch total limit reached (1024)");
}

/// A register against an unknown target is a hard error, never an empty
/// watch (the TS `/No direct child/` pin).
#[tokio::test]
async fn agent_watch_register_rejects_unknown_child() {
    let (tx, _rx) = mpsc::unbounded_channel();
    let state = Arc::new(FakeWatchState::default());
    let sessions = watch_sessions(tx, state).await;
    let error = sessions
        .watch_agent("ghost".to_string())
        .await
        .expect_err("an unmatched target is a hard error");
    assert_eq!(error.to_string(), "No direct child matches \"ghost\"");
}

/// Cancel is idempotent (`false` the second time) and published events
/// stay readable after the cancel (nano-rlm: "published inbox events
/// stay readable").
#[test]
fn agent_watch_cancel_is_idempotent_and_keeps_published_events() {
    let mut registry = AgentWatchRegistry::default();
    registry
        .register("sub-1", "researcher", Some(3), 1_000)
        .expect("register");
    assert!(registry
        .apply_snapshot("watch-agent-sub-1", &Ok((3, "idle")), 1_100)
        .is_none());
    let event = registry
        .apply_snapshot("watch-agent-sub-1", &Ok((7, "running")), 1_200)
        .expect("growth");
    registry.push_event(event);
    let before = registry.read(0, 100);
    assert_eq!(before.events.len(), 1);
    assert!(registry.cancel("watch-agent-sub-1"));
    assert!(
        !registry.cancel("watch-agent-sub-1"),
        "the second cancel is false"
    );
    assert!(
        !registry.cancel("watch-agent-unknown"),
        "an unknown id is false"
    );
    let after = registry.read(0, 100);
    assert_eq!(
        after.events.len(),
        1,
        "published events stay readable after the cancel"
    );
    // A cancelled subscription stops emitting.
    assert!(registry
        .apply_snapshot("watch-agent-sub-1", &Ok((9, "running")), 1_300)
        .is_none());
}

/// Restart survival, pinned at the seam that owns it: the registry and
/// its poller live in the parent session's worker process, and the
/// kernel (a child process of this worker) never holds them — so the
/// post-restart read is exactly a fresh host call re-listing the
/// registry (`kernel_stop_revive.rs` exercises the kernel side; the
/// watch registry is not reachable from the kernel at all).
#[tokio::test]
async fn agent_watch_survives_kernel_restart_relist() {
    let (tx, _rx) = mpsc::unbounded_channel();
    let state = Arc::new(FakeWatchState::default());
    let sessions = watch_sessions(tx, state).await;
    push_child(&sessions, "sub-1", "live-1").await;
    let registered = sessions
        .watch_agent("sub-1".to_string())
        .await
        .expect("register against the roster row");
    assert_eq!(registered.status, "active");
    // The kernel ends and revives here: nothing about the registry
    // crossed that boundary, and the fresh host call re-reads it intact.
    let relisted = sessions.watch_agent_list().await.expect("relist");
    assert_eq!(relisted.len(), 1);
    assert_eq!(relisted[0].id, "watch-agent-sub-1");
    assert_eq!(relisted[0].status, "active");
    assert_eq!(relisted[0].child_name, "sub-1-worker");
}

/// Owner teardown: the close walk cancels every subscription (nano-rlm
/// "owner termination cancels subscriptions") and retires the poller.
#[tokio::test]
async fn agent_watch_owner_teardown_cancels_every_subscription() {
    let (tx, _rx) = mpsc::unbounded_channel();
    let state = Arc::new(FakeWatchState::default());
    let sessions = watch_sessions(tx, state).await;
    push_child(&sessions, "sub-1", "live-1").await;
    push_child(&sessions, "sub-2", "live-2").await;
    sessions
        .watch_agent("sub-1".to_string())
        .await
        .expect("register one");
    sessions
        .watch_agent("sub-2".to_string())
        .await
        .expect("register two");
    sessions
        .close_children(ChildCloseReason::Killed)
        .await
        .expect("close the parent session's children");
    let rows = sessions
        .watch_agent_list()
        .await
        .expect("relist after close");
    assert_eq!(rows.len(), 2, "rows stay inspectable after teardown");
    assert!(rows.iter().all(|row| row.status == "cancelled"));
    // A cancelled registry registers nothing new post-teardown — the
    // subscriptions died with the owner.
}

/// One coalesced quiet wake row per poll batch: two children growing in
/// one tick produce ONE `display: false` `agent_watch_notice` row and
/// two ring events; a quiet tick produces no row at all.
#[tokio::test]
async fn agent_watch_one_wake_row_per_batch() {
    let (sessions, mut follow_up_rx, state) = quick_watch_sessions().await;
    push_child(&sessions, "sub-1", "live-1").await;
    push_child(&sessions, "sub-2", "live-2").await;
    sessions
        .watch_agent("sub-1".to_string())
        .await
        .expect("register one");
    sessions
        .watch_agent("sub-2".to_string())
        .await
        .expect("register two");
    // Let the poller run quiet ticks first (baseline ticks and any
    // link warm-up), so the growth lands inside one tick deterministically.
    tokio::time::sleep(Duration::from_millis(100)).await;
    // Both children grow in the same tick.
    state.count("live-1", 5);
    state.count("live-2", 4);
    let row = tokio::time::timeout(Duration::from_secs(10), follow_up_rx.recv())
        .await
        .expect("the batch must deliver one wake row")
        .expect("the follow_up channel stays open");
    assert_eq!(row["type"], "follow_up");
    assert_eq!(row["activeSessionId"], "parent-live");
    let custom = &row["customMessage"];
    assert_eq!(custom["role"], "custom");
    assert_eq!(custom["customType"], "agent_watch_notice");
    assert_eq!(custom["display"], false, "the wake row is quiet");
    assert!(
        row["rlmNoticeNonce"].as_str().is_some(),
        "the reserved-kind row rides the one-shot capability"
    );
    let events = custom["details"]["events"].as_array().unwrap();
    assert_eq!(
        events.len(),
        2,
        "one line per child, one row per batch: {custom:?}"
    );
    let by_child: HashMap<&str, &Value> = events
        .iter()
        .map(|event| (event["childId"].as_str().unwrap(), event))
        .collect();
    assert_eq!(by_child["sub-1"]["fromIndex"], 0);
    assert_eq!(by_child["sub-1"]["toIndex"], 5);
    assert_eq!(by_child["sub-2"]["fromIndex"], 0);
    assert_eq!(by_child["sub-2"]["toIndex"], 4);
    assert!(
        custom["content"]
            .as_str()
            .unwrap()
            .contains("[watch-agent child:sub-1-worker] messages 0..5 (+5)"),
        "the row's text is the batched range lines: {custom:?}"
    );
    // The readable ring carries the same batch as N events.
    let page = sessions.watch_read(0, 100).await.expect("read the ring");
    assert_eq!(page.events.len(), 2);
    assert_eq!(page.next_seq, 2);
    assert_eq!(page.dropped, 0);
    // A quiet tick (no further growth) emits no row and no events.
    assert!(
        tokio::time::timeout(Duration::from_millis(300), follow_up_rx.recv())
            .await
            .is_err(),
        "no second wake row may arrive"
    );
    let still = sessions
        .watch_read(2, 100)
        .await
        .expect("read past the batch");
    assert!(still.events.is_empty());
}
