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

fn registered_shells(artifact_dir: &Path) -> Vec<Shell> {
    let Ok(raw) = std::fs::read(artifact_dir.join(REGISTRY_FILE)) else {
        return Vec::new();
    };
    let Ok(registry) = serde_json::from_slice::<Registry>(&raw) else {
        return Vec::new();
    };
    registry.shells
}

fn pid_alive(pid: u32) -> bool {
    matches!(crate::lease::is_process_alive(pid), Ok(true))
}

/// Whether the shell's pid still runs under the recorded start identity.
fn runs_as_registered(shell: &Shell) -> bool {
    pid_alive(shell.pid)
        && crate::lease::get_process_start_id(shell.pid).as_deref() == Some(shell.start_id.as_str())
}

/// The session's live persistent shells as rows with the kernel bash
/// activity row field names: a recycled pid or an unreadable identity never
/// counts as live.
pub(crate) fn live_shell_rows(artifact_dir: &Path) -> Vec<Value> {
    registered_shells(artifact_dir)
        .into_iter()
        .filter(runs_as_registered)
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

/// Kill the session's persistent shells (group TERM to the live ones, then
/// [`kill_groups`] after [`TERM_GRACE`] or once every leader exited) and
/// remove the registry and the shells' files.
pub(crate) fn kill_session_shells(artifact_dir: &Path) {
    let shells = registered_shells(artifact_dir);
    for shell in shells.iter().filter(|shell| runs_as_registered(shell)) {
        let _ = signal_process_group(shell.pid as i32, Signal::Term);
    }
    let deadline = Instant::now() + TERM_GRACE;
    while Instant::now() < deadline && shells.iter().any(runs_as_registered) {
        std::thread::sleep(Duration::from_millis(50));
    }
    kill_groups(&shells);
    let _ = std::fs::remove_file(artifact_dir.join(REGISTRY_FILE));
    let _ = std::fs::remove_dir_all(artifact_dir.join(FILES_DIR));
}

/// SIGKILL each shell's group unless its pid now runs under another identity.
/// A dead leader's group still gets it: members that outlive the leader keep
/// the pgid reserved, because a dead pid can only be some other process's
/// pgid if a new process with that pid created the group. So an existing
/// group whose leader is dead holds the original shell's stragglers, and a
/// group that no longer exists makes the kill fail with ESRCH.
fn kill_groups(shells: &[Shell]) {
    for shell in shells {
        if !pid_alive(shell.pid) || runs_as_registered(shell) {
            let _ = signal_process_group(shell.pid as i32, Signal::Kill);
        }
    }
}

#[cfg(all(test, unix))]
pub(crate) mod test_support {
    use std::path::Path;
    use std::process::{Child, Command};

    /// A setsid'd `sleep` registered in `agent_dir`'s persistent-shell home,
    /// assigned to `session_id`, under `start_id` (`None`: its real start identity).
    pub(crate) fn registered_sleeper(
        agent_dir: &Path,
        session_id: &str,
        start_id: Option<&str>,
    ) -> Child {
        let mut command = Command::new("sleep");
        command.arg("60");
        pa_core::platform::process::set_new_session(&mut command);
        let child = command.spawn().expect("spawn sleeper");
        let start_id = start_id.map_or_else(
            || crate::lease::get_process_start_id(child.id()).expect("start id"),
            str::to_string,
        );
        let home = agent_dir.join(super::HOME_DIR);
        std::fs::create_dir_all(&home).expect("persistent-shell home");
        let pid = child.id();
        let id = format!("{pid:032}");
        let record = serde_json::json!({
            "id": id,
            "pid": pid,
            "startId": start_id,
            "command": "sleep 60",
            "startedAt": "2026-01-01T00:00:00+00:00",
            "sessionId": session_id,
        });
        std::fs::write(home.join(format!("{id}.json")), record.to_string()).expect("write record");
        child
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::test_support::registered_sleeper;
    use super::*;

    #[test]
    fn session_delete_leaves_its_shells_running_as_orphans() {
        let root = tempfile::tempdir().unwrap();
        let sessions = root.path().join("sessions");
        std::fs::create_dir_all(&sessions).unwrap();
        let session_file = sessions.join("s1.jsonl");
        std::fs::write(&session_file, "{}\n").unwrap();
        let mut sleeper = registered_sleeper(root.path(), "s1", None);

        let deleted = crate::saved_session_commands::delete_session_file(&session_file);

        assert_eq!(deleted["ok"], true);
        assert!(!session_file.exists());
        assert!(
            sleeper.try_wait().unwrap().is_none(),
            "the shell still runs"
        );
        assert_eq!(
            live_shell_rows_by_session(root.path())["s1"][0]["pid"],
            sleeper.id(),
            "it stays listed under the deleted session"
        );
        sleeper.kill().unwrap();
        sleeper.wait().unwrap();
    }

    #[test]
    fn recycled_pid_is_not_reported() {
        let root = tempfile::tempdir().unwrap();
        let mut stranger = registered_sleeper(root.path(), "s1", Some("proc:not-this-process"));

        assert!(live_shell_rows_by_session(root.path()).is_empty());
        stranger.kill().unwrap();
        stranger.wait().unwrap();
    }
}
