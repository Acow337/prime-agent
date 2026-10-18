//! Headless e2e for the agents view's machine-wide persistent shells: the list's
//! `Persistent shells` block; Enter on a shell whose session exists opens that session on its Bash
//! panel, Enter on an orphan opens the Bash list over the orphans with output and stop.
#![cfg(unix)]

use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::PathBuf;
use std::time::Duration;

use pa_tui::agents_view::{
    run_agents_view, AgentsHeadlessPlan, AgentsStep, AgentsViewOptions, AgentsViewOutcome,
    AgentsViewUiMode,
};
use pa_tui::interactive::{SessionPanel, SessionSelection};
use serde_json::{json, Value};

// Headless mouse tracking is process-global; all plans in this binary serialize.
static RUN_LOCK: tokio::sync::Mutex<()> = tokio::sync::Mutex::const_new(());

fn shell(id: &str, command: &str, session_id: &str, status: &str) -> Value {
    json!({ "id": id, "command": command, "pid": 4242, "startedAt": "2026-01-01T00:00:00+00:00",
            "durationMs": 12_400, "status": status, "persistent": true, "sessionId": session_id })
}

/// Serves the roster (one live session, `release prep`), an empty saved catalog, and three shells:
/// `build-watch` assigned to the live session, the orphans `long-job` and `orphan-watch` to deleted
/// ones. A kill finishes the shell in later lists (`long-job`'s kill fails late, delivered with
/// `orphan-watch`'s next output read); returns the killed shell ids.
fn serve(listener: &UnixListener) -> Vec<String> {
    let (stream, _) = listener.accept().expect("accept view connection");
    stream
        .set_read_timeout(Some(Duration::from_millis(100)))
        .expect("read timeout");
    let mut writer = stream.try_clone().expect("clone socket");
    let mut reader = BufReader::new(stream);
    write_line(
        &mut writer,
        &json!({
            "type": "daemon_hello",
            "protocol": { "name": "prime-agent.daemon", "version": 7 },
            "serverCapabilities": ["persistent_shells"],
        }),
    );
    let mut killed = Vec::new();
    let mut pending_kill_failure: Option<String> = None;
    loop {
        let Some(line) = read_line(&mut reader) else {
            return killed;
        };
        let Ok(envelope) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let id = envelope["id"].as_str().unwrap_or_default();
        let command = &envelope["command"];
        let command_type = command["type"].as_str().unwrap_or_default();
        let data = match command_type {
            "roster_subscribe" => json!({ "roster": [{
                "agentId": "p",
                "status": "idle",
                "summary": {
                    "sessionId": "p",
                    "lifecycle": "live",
                    "activeSessionId": "p-live",
                    "sessionFile": "/tmp/p.jsonl",
                    "runtimeKind": "top-level",
                    "sessionName": "release prep",
                    "messageCount": 2,
                    "rlmDepth": 0,
                },
            }] }),
            "list_saved_sessions" => json!({ "sessions": [] }),
            "list_persistent_shells" => {
                let status = |id: &str| {
                    if killed.iter().any(|killed| killed == id) {
                        "finished"
                    } else {
                        "running"
                    }
                };
                json!({ "activities": [
                    shell("b", "build-watch", "p", status("b")),
                    shell("l", "long-job", "gone", status("l")),
                    shell("o", "orphan-watch", "gone-too", status("o")),
                ] })
            }
            "tail_persistent_shell" => {
                // Deliver the old shell's error only once another shell's
                // pane has opened and requested its output.
                if command["shellId"] == "o" {
                    if let Some(kill_id) = pending_kill_failure.take() {
                        respond_failure(
                            &mut writer,
                            &kill_id,
                            "kill_persistent_shell",
                            "old shell kill failed",
                        );
                    }
                }
                json!({ "shellId": command["shellId"], "tail": "watching..." })
            }
            "kill_persistent_shell" => {
                if command["shellId"] == "l" {
                    pending_kill_failure = Some(id.to_string());
                    continue;
                }
                killed.push(command["shellId"].as_str().unwrap_or_default().to_string());
                json!({ "shellId": command["shellId"] })
            }
            "roster_unsubscribe" => Value::Null,
            other => {
                respond_failure(&mut writer, id, other, "not handled by the mock");
                continue;
            }
        };
        respond(&mut writer, id, command_type, &data);
    }
}

fn write_line(writer: &mut UnixStream, value: &Value) {
    let mut line = serde_json::to_string(value).expect("serialize line");
    line.push('\n');
    writer.write_all(line.as_bytes()).expect("write");
    writer.flush().expect("flush");
}

fn respond(writer: &mut UnixStream, id: &str, command: &str, data: &Value) {
    write_line(
        writer,
        &json!({
            "id": id,
            "type": "response",
            "command": command,
            "success": true,
            "data": data,
        }),
    );
}

fn respond_failure(writer: &mut UnixStream, id: &str, command: &str, error: &str) {
    write_line(
        writer,
        &json!({
            "id": id,
            "type": "response",
            "command": command,
            "success": false,
            "error": error,
        }),
    );
}

/// One line with a bounded quiet window; `None` ends the serve loop on EOF or the quiet cap.
fn read_line(reader: &mut BufReader<UnixStream>) -> Option<String> {
    const QUIET_WINDOW_MS: u32 = 90;
    let mut quiet_windows: u32 = 0;
    let mut line = String::new();
    loop {
        match reader.read_line(&mut line) {
            Ok(0) => return None,
            Ok(_) if line.trim().is_empty() => {
                line.clear();
            }
            Ok(_) => return Some(line),
            Err(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::WouldBlock | std::io::ErrorKind::TimedOut
                ) =>
            {
                quiet_windows += 1;
                if quiet_windows >= QUIET_WINDOW_MS {
                    return None;
                }
                std::thread::sleep(Duration::from_millis(50));
                line.clear();
            }
            Err(_) => return None,
        }
    }
}

fn view_options(socket: &std::path::Path) -> AgentsViewOptions {
    AgentsViewOptions {
        socket_path: socket.to_path_buf(),
        cwd: PathBuf::from("/tmp"),
        session_dir: Some(PathBuf::from("/tmp/sessions")),
        theme: "prime".to_string(),
        version: "0.0.0".to_string(),
        anchor_session_id: None,
        scope: None,
        query: None,
        expanded_ancestors: Vec::new(),
        selected_row_identity: None,
        selected_key: None,
        status_message: None,
        keybindings: pa_tui::keybindings::KeybindingsManager::new(),
        show_hardware_cursor: false,
        incident_notice_state: None,
        create_config: serde_json::json!({}),
    }
}

/// Run one headless plan against the mock; returns the outcome and the killed shell ids.
async fn run_plan(steps: Vec<AgentsStep>) -> (AgentsViewOutcome, Vec<String>) {
    let _guard = RUN_LOCK.lock().await;
    let dir = tempfile::TempDir::new().expect("temp dir");
    let socket = dir.path().join("agents-view.sock");
    let listener = UnixListener::bind(&socket).expect("bind mock socket");
    let server = std::thread::spawn(move || serve(&listener));
    let plan = AgentsHeadlessPlan {
        steps,
        width: 100,
        height: 30,
    };
    let outcome = Box::pin(run_agents_view(
        view_options(&socket),
        AgentsViewUiMode::Headless(plan),
        None,
    ))
    .await
    .expect("the agents view run")
    .outcome;
    (outcome, server.join().expect("mock"))
}

fn wait_render(needle: &str) -> AgentsStep {
    AgentsStep::WaitRender {
        needle: needle.to_string(),
        timeout_ms: 8000,
    }
}

#[tokio::test]
async fn shell_rows_are_table_rows() {
    let (outcome, _) = run_plan(vec![
        AgentsStep::WaitSettle { timeout_ms: 2500 },
        wait_render("$ orphan-watch"),
    ])
    .await;

    let list = outcome
        .frames
        .iter()
        .find(|frame| frame.contains("Persistent shells (3)"))
        .expect("a list frame");
    for (command, tokens) in [
        ("$ build-watch", ["$", "build-watch", "-", "-", "12.4s"]),
        ("$ long-job", ["$", "long-job", "-", "-", "12.4s"]),
        ("$ orphan-watch", ["$", "orphan-watch", "-", "-", "12.4s"]),
    ] {
        let row = list
            .lines()
            .find(|line| line.contains(command))
            .unwrap_or_else(|| panic!("{command:?} in:\n{list}"));
        assert_eq!(row.split_whitespace().collect::<Vec<_>>(), tokens, "{list}");
    }
}

#[tokio::test]
async fn enter_on_a_live_sessions_shell_opens_that_session_on_its_bash_panel() {
    let (outcome, killed) = run_plan(vec![
        AgentsStep::WaitSettle { timeout_ms: 2500 },
        wait_render("$ orphan-watch"),
        AgentsStep::Key("home".to_string()),
        AgentsStep::Key("enter".to_string()),
    ])
    .await;

    assert_eq!(
        outcome.selection,
        Some(SessionSelection::Attach("p-live".to_string()))
    );
    assert!(
        matches!(outcome.panel, Some(SessionPanel::Shells { .. })),
        "the session opens on its Bash panel: {:?}",
        outcome.panel
    );
    assert!(
        !outcome
            .frames
            .iter()
            .any(|frame| frame.contains("watching...") || frame.contains("pid 4242")),
        "no standalone shell overlay:\n{}",
        outcome.frames.join("\n---\n")
    );
    assert!(killed.is_empty());
}

#[tokio::test]
async fn enter_on_an_orphan_lists_the_orphans_and_stops_one() {
    let (outcome, killed) = run_plan(vec![
        AgentsStep::WaitSettle { timeout_ms: 2500 },
        wait_render("$ orphan-watch"),
        AgentsStep::Key("home".to_string()),
        AgentsStep::Key("down".to_string()),
        AgentsStep::Key("down".to_string()),
        AgentsStep::Key("enter".to_string()),
        wait_render("Session deleted"),
        AgentsStep::Key("enter".to_string()),
        wait_render("watching..."),
        AgentsStep::Key("enter".to_string()),
        wait_render("\u{25cb} finished"),
        AgentsStep::Key("left".to_string()),
        wait_render("1 running"),
        AgentsStep::Key("escape".to_string()),
        wait_render("Persistent shells (2)"),
    ])
    .await;
    let frames = &outcome.frames;

    assert!(outcome.selection.is_none(), "an orphan opens no session");
    let orphans = frames
        .iter()
        .find(|frame| frame.contains("Session deleted"))
        .expect("the orphan list frame");
    let lines: Vec<&str> = orphans.lines().collect();
    let title = lines
        .iter()
        .position(|line| line.trim_start().starts_with("Bash"))
        .unwrap_or_else(|| panic!("the Bash title in:\n{orphans}"));
    assert!(lines[title].trim_end().ends_with("2 running"), "{orphans}");
    assert_eq!(lines[title + 1].trim(), "Session deleted", "{orphans}");
    let selected = lines
        .iter()
        .find(|line| line.starts_with('\u{203a}'))
        .unwrap_or_else(|| panic!("a selected row in:\n{orphans}"));
    assert!(selected.contains("orphan-watch"), "{orphans}");
    assert!(
        orphans.contains("long-job") && !orphans.contains("build-watch"),
        "only the orphans list:\n{orphans}"
    );
    for detail in frames.iter().filter(|frame| frame.contains("pid 4242")) {
        assert!(
            !detail.to_lowercase().contains("session"),
            "no session item in the detail:\n{detail}"
        );
    }

    assert_eq!(killed, ["o"], "the stop named the Entered orphan");
    let stopped = frames
        .iter()
        .find(|frame| frame.contains("\u{25cb} finished"))
        .expect("the stopped detail frame");
    assert!(
        stopped.contains("orphan-watch") && !stopped.contains("Cancel command"),
        "the detail stays on the stopped shell:\n{stopped}"
    );
    let back = frames
        .iter()
        .rev()
        .find(|frame| frame.contains("1 running"))
        .expect("the list after back");
    assert!(!back.contains("orphan-watch"), "{back}");
    let last = frames.last().expect("a frame");
    assert!(
        !last.contains("$ orphan-watch") && last.contains("$ long-job"),
        "the stopped orphan left the block:\n{last}"
    );
}

#[tokio::test]
async fn clicking_an_orphan_opens_the_bash_list_which_owns_clicks() {
    let (baseline, _) = run_plan(vec![
        AgentsStep::WaitSettle { timeout_ms: 2500 },
        wait_render("$ orphan-watch"),
    ])
    .await;
    let row = baseline
        .frames
        .last()
        .expect("list frame")
        .lines()
        .position(|line| line.contains("$ long-job"))
        .expect("shell row");
    let (outcome, killed) = run_plan(vec![
        AgentsStep::WaitSettle { timeout_ms: 2500 },
        wait_render("$ orphan-watch"),
        AgentsStep::Click { row, col: 3 },
        wait_render("Session deleted"),
        // Click where the hidden session row sits: the overlay owns it.
        AgentsStep::Click {
            row: row + 4,
            col: 3,
        },
    ])
    .await;
    assert!(outcome
        .frames
        .last()
        .expect("overlay frame")
        .contains("Session deleted"));
    assert!(outcome.selection.is_none() && killed.is_empty());
}

#[tokio::test]
async fn a_late_kill_failure_does_not_move_to_another_shell_pane() {
    let (outcome, _) = run_plan(vec![
        AgentsStep::WaitSettle { timeout_ms: 2500 },
        wait_render("$ orphan-watch"),
        AgentsStep::Key("home".to_string()),
        AgentsStep::Key("down".to_string()),
        AgentsStep::Key("enter".to_string()),
        wait_render("Session deleted"),
        AgentsStep::Key("enter".to_string()),
        wait_render("watching..."),
        AgentsStep::Key("enter".to_string()),
        AgentsStep::Key("left".to_string()),
        AgentsStep::Key("down".to_string()),
        AgentsStep::Key("enter".to_string()),
        wait_render("watching..."),
    ])
    .await;
    let frames = &outcome.frames;
    assert!(frames
        .last()
        .expect("shell o frame")
        .contains("orphan-watch"));
    assert!(
        !frames
            .iter()
            .any(|frame| frame.contains("old shell kill failed")),
        "shell l's failure must not appear on shell o"
    );
}

#[tokio::test]
async fn ctrl_x_on_a_shell_row_stops_it_and_its_finished_row_leaves_the_list() {
    let (outcome, killed) = run_plan(vec![
        AgentsStep::WaitSettle { timeout_ms: 2500 },
        wait_render("$ orphan-watch"),
        AgentsStep::Key("home".to_string()),
        wait_render("open   Ctrl+X stop"),
        AgentsStep::Key("ctrl+x".to_string()),
        wait_render("Press Ctrl+X again to stop"),
        AgentsStep::Key("ctrl+x".to_string()),
        wait_render("Persistent shells (2)"),
    ])
    .await;

    assert_eq!(killed, ["b"], "the stop named the selected shell");
    let last = outcome.frames.last().expect("a frame");
    assert!(
        last.contains("Stopped build-watch") && !last.contains("$ build-watch"),
        "the stopped shell left the list:\n{last}"
    );
}
