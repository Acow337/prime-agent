//! The kernel's persistent `bash()` shells (prime-agent-runtime `rlm/bash.py`,
//! `bash(..., persistent=True)`): the kernel registers each one in
//! `<session artifact dir>/persistent-shells.json` and keeps its output and exit
//! status under `persistent-shells/`. The daemon reads that registry to list a
//! saved session's live shells, and kills and removes them when the session is
//! deleted or archived.

use std::path::Path;
use std::time::{Duration, Instant};

use pa_core::platform::process::{signal_process_group, Signal};
use serde::Deserialize;
use serde_json::{json, Value};

const REGISTRY_FILE: &str = "persistent-shells.json";
const FILES_DIR: &str = "persistent-shells";
/// How long a shell may exit on its own after SIGTERM before its group gets SIGKILL.
const TERM_GRACE: Duration = Duration::from_secs(2);
/// The kernel activity rows cap the command at the same length.
const COMMAND_CAP: usize = 512;

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct Shell {
    id: String,
    pid: u32,
    start_id: String,
    command: String,
    started_at: String,
}

#[derive(Deserialize)]
struct Registry {
    shells: Vec<Shell>,
}

/// The registered shells whose pid still runs under the recorded start
/// identity: a recycled pid or an unreadable identity never counts.
fn live_shells(artifact_dir: &Path) -> Vec<Shell> {
    let Ok(raw) = std::fs::read(artifact_dir.join(REGISTRY_FILE)) else {
        return Vec::new();
    };
    let Ok(registry) = serde_json::from_slice::<Registry>(&raw) else {
        return Vec::new();
    };
    registry
        .shells
        .into_iter()
        .filter(|shell| {
            matches!(crate::lease::is_process_alive(shell.pid), Ok(true))
                && crate::lease::get_process_start_id(shell.pid).as_deref()
                    == Some(shell.start_id.as_str())
        })
        .collect()
}

/// The session's live persistent shells as rows with the kernel bash
/// activity row field names.
pub(crate) fn live_shell_rows(artifact_dir: &Path) -> Vec<Value> {
    live_shells(artifact_dir)
        .into_iter()
        .map(|shell| {
            json!({
                "id": shell.id,
                "command": shell.command.chars().take(COMMAND_CAP).collect::<String>(),
                "pid": shell.pid,
                "startedAt": shell.started_at,
                "status": "running",
                "persistent": true,
            })
        })
        .collect()
}

/// Kill the session's live persistent shells (group TERM, then group KILL
/// after [`TERM_GRACE`] or once every leader exited) and remove the registry
/// and the shells' files.
pub(crate) fn kill_session_shells(artifact_dir: &Path) {
    let shells = live_shells(artifact_dir);
    if !shells.is_empty() {
        for shell in &shells {
            let _ = signal_process_group(shell.pid as i32, Signal::Term);
        }
        let deadline = Instant::now() + TERM_GRACE;
        while Instant::now() < deadline
            && shells
                .iter()
                .any(|shell| matches!(crate::lease::is_process_alive(shell.pid), Ok(true)))
        {
            std::thread::sleep(Duration::from_millis(50));
        }
        // Group members that outlive the leader keep the pgid reserved.
        for shell in &shells {
            let _ = signal_process_group(shell.pid as i32, Signal::Kill);
        }
    }
    let _ = std::fs::remove_file(artifact_dir.join(REGISTRY_FILE));
    let _ = std::fs::remove_dir_all(artifact_dir.join(FILES_DIR));
}

#[cfg(all(test, unix))]
pub(crate) mod test_support {
    use std::path::Path;
    use std::process::{Child, Command};

    /// A setsid'd `sleep` registered in `artifact_dir`'s registry under
    /// `start_id` (`None`: its real start identity).
    pub(crate) fn registered_sleeper(artifact_dir: &Path, start_id: Option<&str>) -> Child {
        let mut command = Command::new("sleep");
        command.arg("60");
        pa_core::platform::process::set_new_session(&mut command);
        let child = command.spawn().expect("spawn sleeper");
        let start_id = start_id.map_or_else(
            || crate::lease::get_process_start_id(child.id()).expect("start id"),
            str::to_string,
        );
        std::fs::create_dir_all(artifact_dir.join(super::FILES_DIR)).expect("files dir");
        let registry = serde_json::json!({ "shells": [{
            "id": "a".repeat(32),
            "pid": child.id(),
            "startId": start_id,
            "command": "sleep 60",
            "startedAt": "2026-01-01T00:00:00+00:00",
        }] });
        std::fs::write(
            artifact_dir.join(super::REGISTRY_FILE),
            registry.to_string(),
        )
        .expect("write registry");
        child
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::test_support::registered_sleeper;
    use super::*;

    #[test]
    fn session_delete_kills_live_shells_and_removes_their_files() {
        let root = tempfile::tempdir().unwrap();
        let sessions = root.path().join("sessions");
        std::fs::create_dir_all(&sessions).unwrap();
        let session_file = sessions.join("s1.jsonl");
        std::fs::write(&session_file, "{}\n").unwrap();
        let artifacts = root.path().join("session-artifacts").join("s1");
        let mut sleeper = registered_sleeper(&artifacts, None);

        let deleted = crate::saved_session_commands::delete_session_file(&session_file);

        assert_eq!(deleted["ok"], true);
        let status = sleeper.wait().unwrap();
        assert!(!status.success(), "the shell was killed, not finished");
        assert!(!artifacts.exists());
    }

    #[test]
    fn recycled_pid_is_neither_reported_nor_killed() {
        let artifacts = tempfile::tempdir().unwrap();
        let mut stranger = registered_sleeper(artifacts.path(), Some("proc:not-this-process"));

        assert!(live_shell_rows(artifacts.path()).is_empty());
        kill_session_shells(artifacts.path());

        assert!(stranger.try_wait().unwrap().is_none(), "never signalled");
        assert!(!artifacts.path().join(REGISTRY_FILE).exists());
        stranger.kill().unwrap();
        stranger.wait().unwrap();
    }
}
