//! The kernel's persistent `bash()` shells (prime-agent-runtime `rlm/bash.py`,
//! `bash(..., persistent=True)`): every session's shells live in one home,
//! `<agent dir>/persistent-shells/`, as a `<id>.json` record assigned to the
//! session that started it, plus the shell's `<id>.out` and `<id>.rc`. The
//! daemon reads the records to list each saved session's live shells. Nothing
//! in the session lifecycle kills them: a deleted session's shells keep
//! running as orphans, still assigned to it.

use std::collections::HashMap;
use std::path::Path;

use serde::Deserialize;
use serde_json::{json, Value};

const HOME_DIR: &str = "persistent-shells";
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
    session_id: String,
}

fn registered_shells(agent_dir: &Path) -> Vec<Shell> {
    let Ok(entries) = std::fs::read_dir(agent_dir.join(HOME_DIR)) else {
        return Vec::new();
    };
    let mut shells: Vec<Shell> = entries
        .flatten()
        .map(|entry| entry.path())
        .filter(|path| path.extension().is_some_and(|ext| ext == "json"))
        .filter_map(|path| std::fs::read(path).ok())
        .filter_map(|raw| serde_json::from_slice(&raw).ok())
        .collect();
    shells.sort_by(|a, b| a.started_at.cmp(&b.started_at));
    shells
}

/// Whether the shell's pid still runs under the recorded start identity.
fn runs_as_registered(shell: &Shell) -> bool {
    matches!(crate::lease::is_process_alive(shell.pid), Ok(true))
        && crate::lease::get_process_start_id(shell.pid).as_deref() == Some(shell.start_id.as_str())
}

/// The live persistent shells by assigned session id, as rows with the
/// kernel bash activity row field names: a recycled pid or an unreadable
/// identity never counts as live.
pub(crate) fn live_shell_rows_by_session(agent_dir: &Path) -> HashMap<String, Vec<Value>> {
    let mut rows: HashMap<String, Vec<Value>> = HashMap::new();
    for shell in registered_shells(agent_dir)
        .into_iter()
        .filter(runs_as_registered)
    {
        rows.entry(shell.session_id).or_default().push(json!({
            "id": shell.id,
            "command": shell.command.chars().take(COMMAND_CAP).collect::<String>(),
            "pid": shell.pid,
            "startedAt": shell.started_at,
            "status": "running",
            "persistent": true,
        }));
    }
    rows
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
        register(agent_dir, child.id(), &start_id, session_id);
        child
    }

    /// Write one record for shell `pid` under `start_id`, assigned to
    /// `session_id`; returns its id.
    pub(crate) fn register(agent_dir: &Path, pid: u32, start_id: &str, session_id: &str) -> String {
        let home = agent_dir.join(super::HOME_DIR);
        std::fs::create_dir_all(&home).expect("persistent-shell home");
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
        id
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

    /// A registered `sleep` leading its own process group, plus one member in
    /// that group.
    fn registered_group(agent_dir: &std::path::Path) -> (Child, Child, String) {
        let mut leader_command = Command::new("sleep");
        leader_command.arg("60");
        pa_core::platform::process::set_new_process_group(&mut leader_command);
        let leader = leader_command.spawn().unwrap();
        let start_id = crate::lease::get_process_start_id(leader.id()).unwrap();
        let id = register(agent_dir, leader.id(), &start_id, "s1");
        let member = Command::new("sleep")
            .arg("60")
            .process_group(leader.id() as i32)
            .spawn()
            .unwrap();
        (leader, member, id)
    }

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
    fn recycled_pid_is_neither_reported_nor_killed() {
        let root = tempfile::tempdir().unwrap();
        let stranger = registered_sleeper(root.path(), "s1", Some("proc:not-this-process"));
        let id = format!("{:032}", stranger.id());

        assert!(live_shell_rows_by_session(root.path()).is_empty());
        assert_eq!(shell_rows(root.path())[0]["status"], "finished");
        assert!(kill_shell(root.path(), &id));

        assert_eq!(
            fatal_signal_after_own_term(stranger),
            Some(NixSignal::SIGTERM as i32),
            "never signalled"
        );
    }

    #[test]
    fn kill_stops_the_whole_group_and_leaves_the_shell_listed_as_finished() {
        let root = tempfile::tempdir().unwrap();
        let (mut leader, member, id) = registered_group(root.path());
        std::fs::write(shell_file(root.path(), &id, "out"), "line\n").unwrap();

        assert!(kill_shell(root.path(), &id));

        assert!(!leader.wait().unwrap().success(), "the leader was killed");
        assert_eq!(
            fatal_signal_after_own_term(member),
            Some(NixSignal::SIGTERM as i32),
            "the group's TERM reached the member"
        );
        assert_eq!(shell_rows(root.path())[0]["status"], "finished");
        assert_eq!(output_tail(root.path(), &id, 1).as_deref(), Some("line"));
        assert!(!kill_shell(root.path(), "unknown"));
    }

    #[test]
    fn exited_leaders_group_is_killed() {
        let root = tempfile::tempdir().unwrap();
        let (mut leader, member, id) = registered_group(root.path());
        leader.kill().unwrap();
        leader.wait().unwrap();

        assert!(kill_shell(root.path(), &id));

        assert_eq!(
            fatal_signal_after_own_term(member),
            Some(NixSignal::SIGKILL as i32),
            "the dead leader's group got SIGKILL"
        );
    }

    #[test]
    fn pid_recycled_during_the_grace_is_not_killed() {
        let root = tempfile::tempdir().unwrap();
        let mut stranger = registered_sleeper(root.path(), "s1", Some("proc:not-this-process"));
        let shells = registered_shells(root.path());

        kill_group(&shells[0]);
        assert!(signal_process_group(stranger.id() as i32, Signal::Term));

        let status = stranger.wait().unwrap();
        assert_eq!(
            status.signal(),
            Some(NixSignal::SIGTERM as i32),
            "no SIGKILL came first"
        );
    }

    #[test]
    fn shell_rows_list_every_shell_with_its_session_and_the_output_tail_reads_the_newest_lines() {
        let root = tempfile::tempdir().unwrap();
        let mut sleeper = registered_sleeper(root.path(), "s1", None);
        let mut done = Command::new("true").spawn().unwrap();
        done.wait().unwrap();
        let finished = register(root.path(), done.id(), "proc:0", "gone");
        std::fs::write(shell_file(root.path(), &finished, "rc"), "3\n").unwrap();
        std::fs::write(
            shell_file(root.path(), &finished, "out"),
            "one\ntwo\nthree\n",
        )
        .unwrap();

        let rows = shell_rows(root.path());

        let row = |id: &str| rows.iter().find(|row| row["id"] == id).unwrap().clone();
        let running = row(&format!("{:032}", sleeper.id()));
        assert_eq!(
            (
                &running["status"],
                &running["sessionId"],
                running.get("exitCode")
            ),
            (&json!("running"), &json!("s1"), None)
        );
        let done_row = row(&finished);
        assert_eq!(
            (
                &done_row["status"],
                &done_row["sessionId"],
                &done_row["exitCode"]
            ),
            (&json!("finished"), &json!("gone"), &json!(3))
        );
        assert_eq!(
            output_tail(root.path(), &finished, 2).as_deref(),
            Some("two\nthree")
        );
        assert_eq!(output_tail(root.path(), "unknown", 2), None);
        sleeper.kill().unwrap();
        sleeper.wait().unwrap();
    }
}
