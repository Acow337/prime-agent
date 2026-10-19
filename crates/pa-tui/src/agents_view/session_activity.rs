//! The per-session shells and factory counts behind the agents-view activity lines.
use std::collections::HashMap;

use super::{ActivityCounts, AgentsViewMode, DaemonClient, DaemonCommand, SessionActivity};
use crate::bash_view::ShellCounts;
use crate::factory_view::FactoryCounts;
use anyhow::Result;
use serde_json::{Map, Value};
use tokio::sync::oneshot;

/// One session's counts from one cycle: `None` is a failed read, which keeps that session's last
/// count.
pub(super) struct ActivityRead {
    pub(super) active_session_id: String,
    pub(super) shells: Option<ShellCounts>,
    pub(super) factory: Option<FactoryCounts>,
}

/// One fetch cycle's reads.
pub(super) struct ActivityCycle {
    pub(super) sessions: Vec<ActivityRead>,
    /// The live persistent shells' counts by assigned `sessionId`: `None` is a failed read, which
    /// keeps the last counts.
    pub(super) persistent_shells: Option<HashMap<String, ShellCounts>>,
}

/// One fetch cycle off the key loop: each live session's kernel answers its own shells list and
/// factory graph (its stored specs and live runs), and the daemon answers the machine-wide live
/// persistent shells; the cycle lands once every read answered.
pub(super) fn spawn_session_activity_fetch(
    client: &DaemonClient,
    sessions: Vec<String>,
) -> oneshot::Receiver<ActivityCycle> {
    let shells_supported = client.supports_server_capability("kernel_bash_activity");
    let factory_supported = client.supports_server_capability("factory_activity");
    let persistent_supported = client.supports_server_capability("persistent_shells");
    let client = client.clone();
    let (tx, rx) = oneshot::channel();
    tokio::spawn(async move {
        let persistent_shells = {
            let client = client.clone();
            async move {
                if !persistent_supported {
                    return Some(HashMap::new());
                }
                client
                    .request_ok(DaemonCommand::ListPersistentShells {
                        id: None,
                        rest: Map::default(),
                    })
                    .await
                    .ok()
                    .map(|data| shell_counts_by_session(&data))
            }
        };
        let mut reads = tokio::task::JoinSet::new();
        for active_session_id in sessions {
            let client = client.clone();
            reads.spawn(async move {
                let shells = async {
                    if !shells_supported {
                        return Some(ShellCounts::default());
                    }
                    let reply = client
                        .request_ok(DaemonCommand::ListKernelBash {
                            id: None,
                            active_session_id: active_session_id.clone(),
                            rest: Map::default(),
                        })
                        .await;
                    count(reply, |data| {
                        Some(crate::bash_view::running_shell_counts(data))
                    })
                };
                let factory = async {
                    if !factory_supported {
                        return Some(FactoryCounts::default());
                    }
                    let reply = client
                        .request_ok(DaemonCommand::FactoryActivity {
                            id: None,
                            active_session_id: active_session_id.clone(),
                            action: "graph".to_string(),
                            run_id: None,
                            spec_id: None,
                            timeout_ms: None,
                            rest: Map::default(),
                        })
                        .await;
                    count(reply, |data| {
                        crate::factory_view::factory_reply_lists_runs(data)
                            .then(|| FactoryCounts::of(data))
                    })
                };
                let (shells, factory) = tokio::join!(shells, factory);
                ActivityRead {
                    active_session_id,
                    shells,
                    factory,
                }
            });
        }
        let (sessions, persistent_shells) = tokio::join!(reads.join_all(), persistent_shells);
        let _ = tx.send(ActivityCycle {
            sessions,
            persistent_shells,
        });
    });
    rx
}

/// A `list_persistent_shells` reply's shell counts, by each shell's assigned `sessionId`.
fn shell_counts_by_session(data: &Value) -> HashMap<String, ShellCounts> {
    let mut rows: HashMap<String, Vec<Value>> = HashMap::new();
    for row in data
        .get("activities")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        if let Some(session_id) = row.get("sessionId").and_then(Value::as_str) {
            rows.entry(session_id.to_string())
                .or_default()
                .push(row.clone());
        }
    }
    rows.into_iter()
        .map(|(session_id, rows)| {
            (
                session_id,
                crate::bash_view::running_shell_counts(&Value::Array(rows)),
            )
        })
        .collect()
}

/// The daemon's no-kernel refusal reads as zero, not a failed read.
fn count<T: Default>(reply: Result<Value>, count: impl Fn(&Value) -> Option<T>) -> Option<T> {
    match reply {
        Ok(data) => count(&data),
        Err(error) if crate::daemon_client::is_kernel_not_running(&error) => Some(T::default()),
        Err(_) => None,
    }
}

impl AgentsViewMode {
    /// The roster's live sessions a fetch cycle asks: saved rows have no kernel.
    pub(super) fn live_session_ids(&self) -> Vec<String> {
        self.roster
            .iter()
            .filter_map(|entry| entry.get("summary")?.get("activeSessionId")?.as_str())
            .filter(|active| !active.is_empty())
            .map(str::to_string)
            .collect()
    }

    /// Fold one cycle into the counts (a failed read keeps its last count) and rebuild only when
    /// a count moved.
    pub(super) fn apply_session_activity(&mut self, cycle: ActivityCycle) {
        let live = cycle
            .sessions
            .into_iter()
            .map(|read| {
                let last = self
                    .session_activity
                    .live
                    .get(&read.active_session_id)
                    .copied()
                    .unwrap_or_default();
                let counts = SessionActivity {
                    shells: read.shells.unwrap_or(last.shells),
                    factory: read.factory.unwrap_or(last.factory),
                };
                (read.active_session_id, counts)
            })
            .collect();
        let activity = ActivityCounts {
            live,
            persistent_shells: cycle
                .persistent_shells
                .unwrap_or_else(|| self.session_activity.persistent_shells.clone()),
        };
        if activity != self.session_activity {
            self.session_activity = activity;
            self.rebuild_rows();
        }
    }
}
