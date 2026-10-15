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
        register(artifact_dir, child.id(), &start_id);
        child
    }

    /// Write `artifact_dir`'s registry with one shell `pid` under `start_id`.
    pub(crate) fn register(artifact_dir: &Path, pid: u32, start_id: &str) {
        std::fs::create_dir_all(artifact_dir.join(super::FILES_DIR)).expect("files dir");
        let registry = serde_json::json!({ "shells": [{
            "id": "a".repeat(32),
            "pid": pid,
            "startId": start_id,
            "command": "sleep 60",
            "startedAt": "2026-01-01T00:00:00+00:00",
        }] });
        std::fs::write(
            artifact_dir.join(super::REGISTRY_FILE),
            registry.to_string(),
        )
        .expect("write registry");
    }
}

#[cfg(all(test, unix))]
mod tests {
    use std::os::unix::process::{CommandExt, ExitStatusExt};
    use std::process::{Child, Command};

    use nix::sys::signal::{kill, Signal as NixSignal};
    use nix::unistd::Pid;

    use super::test_support::{register, registered_sleeper};
    use super::*;

    /// Send `child` our own SIGTERM and return the signal it died by: an
    /// earlier SIGKILL is already pending and wins.
    fn fatal_signal_after_own_term(mut child: Child) -> Option<i32> {
        let _ = kill(Pid::from_raw(child.id() as i32), NixSignal::SIGTERM);
        child.wait().unwrap().signal()
    }

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
        let stranger = registered_sleeper(artifacts.path(), Some("proc:not-this-process"));

        assert!(live_shell_rows(artifacts.path()).is_empty());
        kill_session_shells(artifacts.path());

        assert!(!artifacts.path().join(REGISTRY_FILE).exists());
        assert_eq!(
            fatal_signal_after_own_term(stranger),
            Some(NixSignal::SIGTERM as i32),
            "never signalled"
        );
    }

    #[test]
    fn exited_leaders_group_is_killed() {
        let artifacts = tempfile::tempdir().unwrap();
        let mut leader_command = Command::new("sleep");
        leader_command.arg("60");
        pa_core::platform::process::set_new_process_group(&mut leader_command);
        let mut leader = leader_command.spawn().unwrap();
        let start_id = crate::lease::get_process_start_id(leader.id()).unwrap();
        register(artifacts.path(), leader.id(), &start_id);
        let member = Command::new("sleep")
            .arg("60")
            .process_group(leader.id() as i32)
            .spawn()
            .unwrap();
        leader.kill().unwrap();
        leader.wait().unwrap();

        kill_session_shells(artifacts.path());

        assert!(!artifacts.path().join(REGISTRY_FILE).exists());
        assert!(!artifacts.path().join(FILES_DIR).exists());
        assert_eq!(
            fatal_signal_after_own_term(member),
            Some(NixSignal::SIGKILL as i32),
            "the dead leader's group got SIGKILL"
        );
    }

    #[test]
    fn pid_recycled_during_the_grace_is_not_killed() {
        let artifacts = tempfile::tempdir().unwrap();
        let mut stranger = registered_sleeper(artifacts.path(), Some("proc:not-this-process"));
        let raw = std::fs::read(artifacts.path().join(REGISTRY_FILE)).unwrap();
        let registry: Registry = serde_json::from_slice(&raw).unwrap();

        kill_groups(&registry.shells);
        assert!(signal_process_group(stranger.id() as i32, Signal::Term));

        let status = stranger.wait().unwrap();
        assert_eq!(
            status.signal(),
            Some(nix::sys::signal::Signal::SIGTERM as i32),
            "no SIGKILL came first"
        );
    }
}
