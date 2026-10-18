//! The machine-wide persistent shells: the rows under the list's `Persistent shells` heading, and
//! the bash view their open mounts over the view (output, kill, and the session each shell is
//! assigned to, `(deleted)` once that session is gone).
use std::collections::HashMap;

use super::{
    mpsc, AgentsViewMode, AgentsViewRow, DaemonClient, DaemonCommand, RowKind, Section, UiInput,
    Value,
};
use crate::agents_view_forest::session_title;
use crate::agents_view_state::{summary_for_record, UnifiedRecord};
use crate::bash_view::{
    format_duration, parse_bash_activities, BashActivity, BashView, BashViewAction,
};

/// The daemon capability that serves the machine-wide shell list, output, and kill.
pub(super) const CAPABILITY: &str = "persistent_shells";

/// The list poll's cadence: the next fetch arms when the last one landed.
pub(super) const POLL_INTERVAL: std::time::Duration = std::time::Duration::from_secs(2);

/// The row identity prefix of a shell row.
const ROW_PREFIX: &str = "shell:";

/// One `list_persistent_shells` fetch; a failed read lands as `None` and keeps the last list.
pub(super) fn spawn_list_fetch(client: &DaemonClient, ui_tx: mpsc::UnboundedSender<UiInput>) {
    let client = client.clone();
    tokio::spawn(async move {
        let data = client
            .request_ok(DaemonCommand::ListPersistentShells {
                id: None,
                rest: serde_json::Map::default(),
            })
            .await
            .ok();
        let _ = ui_tx.send(UiInput::PersistentShellsLoaded(data));
    });
}

/// Run one bash-view action against the daemon: an open or load-more fetches the output tail, a
/// kill kills the shell (the returned handle lets the exit drain wait for it).
pub(super) fn spawn_action(
    client: &DaemonClient,
    ui_tx: mpsc::UnboundedSender<UiInput>,
    action: BashViewAction,
) -> Option<tokio::task::JoinHandle<()>> {
    let client = client.clone();
    match action {
        BashViewAction::OpenDetail { id, generation } => Some(spawn_tail(
            client,
            ui_tx,
            id,
            generation,
            crate::bash_view::FIRST_TAIL_LINES,
        )),
        BashViewAction::LoadMore {
            id,
            generation,
            lines,
        } => Some(spawn_tail(client, ui_tx, id, generation, lines)),
        BashViewAction::Kill { id } => Some(tokio::spawn(async move {
            let outcome = client
                .request_ok(DaemonCommand::KillPersistentShell {
                    id: None,
                    shell_id: id.clone(),
                    rest: serde_json::Map::default(),
                })
                .await
                .map(|_| ())
                .map_err(|error| format!("Could not kill bash command: {error:#}"));
            let _ = ui_tx.send(UiInput::PersistentShellKilled { id, outcome });
        })),
        BashViewAction::Close | BashViewAction::None => None,
    }
}

fn spawn_tail(
    client: DaemonClient,
    ui_tx: mpsc::UnboundedSender<UiInput>,
    id: String,
    generation: u64,
    lines: u32,
) -> tokio::task::JoinHandle<()> {
    tokio::spawn(async move {
        let outcome = client
            .request_ok(DaemonCommand::TailPersistentShell {
                id: None,
                shell_id: id.clone(),
                lines,
                rest: serde_json::Map::default(),
            })
            .await
            .map(|data| {
                data.get("tail")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string()
            })
            .map_err(|error| format!("Bash output: {error:#}"));
        let _ = ui_tx.send(UiInput::PersistentShellTail {
            id,
            generation,
            outcome,
        });
    })
}

impl AgentsViewMode {
    /// One row per live shell (a finished one only while its bash-view detail is open), its title
    /// the command, its assignment the session's title: `(deleted)` once the loaded catalog and
    /// the roster both lack the session, nothing while it still loads.
    pub(super) fn persistent_shell_rows(&self, records: &[UnifiedRecord]) -> Vec<AgentsViewRow> {
        let titles: HashMap<String, String> = records
            .iter()
            .map(summary_for_record)
            .filter_map(|summary| {
                let session_id = summary.get("sessionId")?.as_str()?.to_string();
                Some((session_id, session_title(&summary)))
            })
            .collect();
        let open = self.shell_view.as_ref().and_then(BashView::detail_id);
        self.persistent_shells
            .get("activities")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|shell| {
                let id = shell.get("id")?.as_str()?;
                if shell.get("status").and_then(Value::as_str) == Some("finished")
                    && open.as_deref() != Some(id)
                {
                    return None;
                }
                let assignment = shell
                    .get("sessionId")
                    .and_then(Value::as_str)
                    .and_then(|session_id| titles.get(session_id).cloned())
                    .or_else(|| self.saved_catalog_loaded.then(|| "(deleted)".to_string()));
                Some(AgentsViewRow {
                    kind: RowKind::PersistentShell,
                    section: Section::Inactive,
                    identity: format!("{ROW_PREFIX}{id}"),
                    parent_identity: None,
                    summary: serde_json::json!({
                        "shellId": id,
                        "assignment": assignment,
                    }),
                    title: shell
                        .get("command")
                        .and_then(Value::as_str)
                        .map(single_line)
                        .unwrap_or_default(),
                    model: "-".to_string(),
                    cost: 0.0,
                    age: format_duration(shell.get("durationMs").and_then(Value::as_u64)),
                    depth: 0,
                    descendant_count: 0,
                    running_subagent_count: 0,
                    expanded: false,
                    has_spawn_code: false,
                })
            })
            .collect()
    }

    /// Enter on a shell row: the bash view over every persistent shell, opened on this one.
    pub(super) fn open_persistent_shell(&mut self, row: &AgentsViewRow) {
        let Some(id) = row.summary.get("shellId").and_then(Value::as_str) else {
            return;
        };
        let mut view = BashView::new(
            activities(&self.persistent_shells, &self.rows),
            self.last_height,
        );
        self.pending_shell_action = Some(view.open(id));
        self.shell_view = Some(view);
    }

    /// One key while the bash view is open: it owns every key; its close returns to the list.
    pub(super) fn handle_shell_view_key(&mut self, key: &str) {
        if key == "ctrl+c" {
            self.exit_guard.note_ctrl_c_handled();
        }
        let Some(view) = self.shell_view.as_mut() else {
            return;
        };
        let open = view.detail_id();
        match view.handle_key(key, &self.keybindings) {
            BashViewAction::Close => self.shell_view = None,
            BashViewAction::None => {}
            action => self.pending_shell_action = Some(action),
        }
        // A finished shell's row lives only while its detail is open.
        if self.shell_view.as_ref().and_then(BashView::detail_id) != open {
            self.rebuild_rows();
        }
    }

    /// A landed list: the rows rebuild and an open bash view follows, only when it changed.
    pub(super) fn apply_persistent_shells(&mut self, data: Value) {
        if data == self.persistent_shells {
            return;
        }
        if let Some(view) = self.shell_view.as_mut() {
            view.clear_error();
        }
        self.persistent_shells = data;
        self.rebuild_rows();
    }
}

/// The bash view's shells: exactly the shells with a row, each naming its row's assignment.
pub(super) fn activities(shells: &Value, rows: &[AgentsViewRow]) -> Vec<BashActivity> {
    parse_bash_activities(shells)
        .into_iter()
        .filter_map(|mut activity| {
            let row = rows.iter().find(|row| {
                row.kind == RowKind::PersistentShell
                    && row.summary.get("shellId").and_then(Value::as_str)
                        == Some(activity.id.as_str())
            })?;
            activity.session = row
                .summary
                .get("assignment")
                .and_then(Value::as_str)
                .map(str::to_string);
            Some(activity)
        })
        .collect()
}

fn single_line(value: &str) -> String {
    crate::menu_panel::scrub_controls(value)
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}
