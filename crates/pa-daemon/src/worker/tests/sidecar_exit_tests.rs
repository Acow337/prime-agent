//! The session-end sidecar pins: a worker that ends a session must leave a
//! current info sidecar — the release fold certifies the file's final
//! generation, so the next process that reads the file folds zero bytes.
use super::*;
use std::path::Path;

/// The sidecar must certify the file's final generation and have consumed
/// it whole: `generation.len == file len && offset == file len`.
fn assert_sidecar_current(label: &str, session_file: &Path) {
    let file_len = std::fs::metadata(session_file)
        .unwrap_or_else(|error| panic!("{label}: the session file: {error}"))
        .len();
    let sidecar = session_file.with_extension("info-cache.json");
    let bytes = std::fs::read(&sidecar).unwrap_or_else(|error| {
        panic!("{label}: no info sidecar beside the ended session: {error}")
    });
    let value: Value = serde_json::from_slice(&bytes).expect("the sidecar is JSON");
    let len = value["state"]["generation"]["len"]
        .as_u64()
        .expect("the sidecar's generation len");
    let offset = value["state"]["offset"]
        .as_u64()
        .expect("the sidecar's offset");
    assert_eq!(
        (len, offset),
        (file_len, file_len),
        "{label}: the sidecar must certify the final generation and have consumed it whole"
    );
}

/// A worker whose turn settled: the create primes nothing (the fresh arm
/// never folds), so the sidecar exists only if the END wrote it — and it
/// must be current, not a warm tail of the rows that landed after the last
/// scan (the kill's own archived row).
#[cfg(unix)]
#[allow(clippy::await_holding_lock)] // the faux registry is process-global: the guard must span the async flow
#[tokio::test]
async fn an_ended_worker_session_leaves_a_current_info_sidecar() {
    let _faux = crate::agent_engine::tests::FAUX_TEST_LOCK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let dir = std::env::temp_dir().join(format!("pa-sidecar-exit-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&dir).unwrap();
    let config = WorkerConfig {
        socket_path: dir.join("worker.sock"),
        supervisor_socket_path: std::path::PathBuf::new(),
        token: "token".to_string(),
        worker_instance_id: String::new(),
        active_session_id: "sidecar-exit-session".to_string(),
        agent_dir: dir.join("agent"),
        recovery_journal_path: dir.join("recovery.jsonl"),
        telemetry_disabled: None,
        script: Some(json!({ "engine": "faux", "responses": ["sidecar reply"] })),
    };
    let worker = std::sync::Arc::new(Worker::new(config, None));
    let created = worker
        .dispatch(
            "create",
            &json!({
                "cwd": dir.to_string_lossy(),
                "sessionDir": dir.join("sessions").to_string_lossy(),
            }),
        )
        .await;
    assert!(created.success, "create failed: {created:?}");
    let session_file = created.data.expect("the create answers a summary")
        ["sessionFile"]
        .as_str()
        .expect("session file")
        .to_string();
    let prompted = worker
        .dispatch(
            "prompt_and_wait",
            &json!({
                "activeSessionId": "sidecar-exit-session",
                "message": "sidecar question",
            }),
        )
        .await;
    assert!(prompted.success, "the prompt failed: {prompted:?}");
    // The scan prime the roster pushes make in production (the push queue is
    // off in this fixture; the orphan-exit e2e covers the push path).
    crate::session_store::read_session_info(Path::new(&session_file));
    let killed = worker.dispatch("kill", &json!({})).await;
    assert!(killed.success, "kill failed: {killed:?}");
    assert_sidecar_current("fresh + turn + kill", Path::new(&session_file));
    let _ = std::fs::remove_dir_all(&dir);
}

/// The bump-day shape: a reopened session over a dead-version sidecar ends
/// with a sidecar at the served version, current to the file's final bytes.
#[cfg(unix)]
#[allow(clippy::await_holding_lock)] // the faux registry is process-global: the guard must span the async flow
#[tokio::test]
async fn a_reopened_session_ends_with_a_current_info_sidecar() {
    let _faux = crate::agent_engine::tests::FAUX_TEST_LOCK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    let dir = std::env::temp_dir().join(format!("pa-sidecar-reopen-{}", uuid::Uuid::new_v4()));
    std::fs::create_dir_all(&dir).unwrap();
    let session_file = dir.join("sessions").join("reopen.jsonl");
    let config = WorkerConfig {
        socket_path: dir.join("worker.sock"),
        supervisor_socket_path: std::path::PathBuf::new(),
        token: "token".to_string(),
        worker_instance_id: String::new(),
        active_session_id: "sidecar-reopen-session".to_string(),
        agent_dir: dir.join("agent"),
        recovery_journal_path: dir.join("recovery.jsonl"),
        telemetry_disabled: None,
        script: Some(json!({ "engine": "faux", "responses": ["first reply"] })),
    };
    let first = std::sync::Arc::new(Worker::new(config, None));
    let created = first
        .dispatch(
            "create",
            &json!({
                "cwd": dir.to_string_lossy(),
                "sessionPath": session_file.to_string_lossy(),
            }),
        )
        .await;
    assert!(created.success, "create failed: {created:?}");
    let prompted = first
        .dispatch(
            "prompt_and_wait",
            &json!({
                "activeSessionId": "sidecar-reopen-session",
                "message": "first question",
            }),
        )
        .await;
    assert!(prompted.success, "the prompt failed: {prompted:?}");
    crate::session_store::read_session_info(&session_file);
    let killed = first.dispatch("kill", &json!({})).await;
    assert!(killed.success, "kill failed: {killed:?}");
    // The dead-version sidecar a newer build must not serve.
    let sidecar = session_file.with_extension("info-cache.json");
    let mut value: Value =
        serde_json::from_slice(&std::fs::read(&sidecar).expect("the first kill wrote a sidecar"))
            .expect("the sidecar is JSON");
    value["version"] = json!(1);
    std::fs::write(&sidecar, serde_json::to_vec(&value).unwrap()).unwrap();

    let second = std::sync::Arc::new(Worker::new(
        WorkerConfig {
            socket_path: dir.join("worker.sock"),
            supervisor_socket_path: std::path::PathBuf::new(),
            token: "token".to_string(),
            worker_instance_id: String::new(),
            active_session_id: "sidecar-reopen-session".to_string(),
            agent_dir: dir.join("agent"),
            recovery_journal_path: dir.join("recovery.jsonl"),
            telemetry_disabled: None,
            script: Some(json!({ "engine": "faux", "responses": ["second reply"] })),
        },
        None,
    ));
    let reopened = second
        .dispatch(
            "create",
            &json!({
                "cwd": dir.to_string_lossy(),
                "sessionPath": session_file.to_string_lossy(),
            }),
        )
        .await;
    assert!(reopened.success, "the reopen failed: {reopened:?}");
    let prompted = second
        .dispatch(
            "prompt_and_wait",
            &json!({
                "activeSessionId": "sidecar-reopen-session",
                "message": "second question",
            }),
        )
        .await;
    assert!(prompted.success, "the prompt failed: {prompted:?}");
    let killed = second.dispatch("kill", &json!({})).await;
    assert!(killed.success, "kill failed: {killed:?}");
    assert_sidecar_current("reopen + turn + kill", &session_file);
    let _ = std::fs::remove_dir_all(&dir);
}
