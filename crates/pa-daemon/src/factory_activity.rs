//! The `factory_activity` worker arm: the `/factory` view's daemon lane.

//! One session-addressed command reaches this session's kernel factory
//! executor (the bridge registered by the pa-core session engine): the
//! payload's `action` rides the out-of-band kernel frame, and the kernel's
//! reply returns verbatim (the run registry stays kernel-owned) — the
//! unscoped graph plus the engine's stored-spec count. The
//! `run` action's model preflight (the allowlist pin, request auth)
//! happens in the session engine before the frame — a doomed run fails
//! before any child spawns.

use std::path::Path;

use serde_json::Value;

use crate::protocol::{response_failure, response_success, DaemonResponse};
use crate::worker::Worker;

/// The `factory_activity` capability's advertisement gate: the factory is
/// opt-in (`factory.enabled` in the shared settings file, default off),
/// so a disabled factory never surfaces its lane — no capability, no
/// dock group, no page. Both hello surfaces (the supervisor's and the
/// worker's) advertise the lane only while the setting reads enabled.
pub(crate) fn factory_lane_enabled(agent_dir: &Path) -> bool {
    pa_core::settings::SettingsManager::create(
        std::env::current_dir().unwrap_or_default(),
        agent_dir,
    )
    .get_factory_enabled()
}

/// The capabilities a hello advertises: the default set, minus the
/// `factory_activity` lane while the factory stays disabled (the opt-in
/// gate; the settings read is fresh per connection, so a `/factory on`
/// toggle surfaces on the next client start).
pub(crate) fn advertised_server_capabilities(agent_dir: &Path) -> Vec<String> {
    let mut capabilities = crate::protocol::default_server_capabilities();
    if !factory_lane_enabled(agent_dir) {
        capabilities.retain(|capability| capability != "factory_activity");
    }
    capabilities
}

impl Worker {
    /// `factory_activity`: one factory action over this session's kernel.
    /// The arm mirrors `handle_kernel_bash_activity`: the engine owns the
    /// kernel scope, and a missing session answers with the same "Kernel
    /// is not running" refusal.
    pub(crate) async fn handle_factory_activity(&self, payload: &Value) -> DaemonResponse {
        if let Err(response) = self.require_created("factory_activity") {
            return response;
        }
        let action = payload
            .get("action")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string();
        if action.is_empty() {
            return response_failure(None, "factory_activity", "action is required", None);
        }
        let run_id = payload
            .get("runId")
            .and_then(Value::as_str)
            .map(str::to_string);
        let spec_id = payload
            .get("specId")
            .and_then(Value::as_str)
            .map(str::to_string);
        let timeout_ms = payload.get("timeoutMs").and_then(Value::as_u64);
        let Some(engine) = &self.agent_engine else {
            return response_failure(
                None,
                "factory_activity",
                pa_types::daemon::KERNEL_NOT_RUNNING_MESSAGE,
                None,
            );
        };
        match engine
            .factory_activity(&action, run_id.as_deref(), spec_id.as_deref(), timeout_ms)
            .await
        {
            Ok(result) => response_success(None, "factory_activity", Some(result)),
            Err(error) => response_failure(None, "factory_activity", &format!("{error:#}"), None),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A live session without a kernel (not booted yet, or passivated) still answers the unscoped
    /// graph: no runs, and its own stored specs from its local harness store — never the global
    /// store's.
    #[tokio::test]
    async fn a_live_session_without_a_kernel_answers_its_stored_specs() {
        let dir = tempfile::TempDir::new().expect("temp dir");
        let sessions = dir.path().join("sessions");
        std::fs::create_dir_all(&sessions).expect("sessions dir");
        let agent_dir = dir.path().join("agent");
        let worker = Worker::new(
            crate::worker::WorkerConfig {
                socket_path: dir.path().join("worker.sock"),
                supervisor_socket_path: std::path::PathBuf::new(),
                token: "token".to_string(),
                worker_instance_id: String::new(),
                active_session_id: "no-kernel".to_string(),
                agent_dir: agent_dir.clone(),
                recovery_journal_path: dir.path().join("recovery.jsonl"),
                telemetry_disabled: Some(true),
                script: Some(serde_json::json!({ "responses": [] })),
            },
            None,
        );
        let created = worker
            .dispatch(
                "create",
                &serde_json::json!({ "cwd": "/tmp", "sessionDir": sessions }),
            )
            .await;
        assert!(created.success, "{created:?}");
        let artifacts = {
            let core = worker.core.lock().unwrap();
            let store = core.store.as_ref().expect("persisted session");
            crate::scheduled_jobs::session_artifact_dir(&store.path, store.session_id())
        };
        crate::test_support::write_factory_specs(
            &pa_core::refinement::get_local_harness_state_dir(artifacts.as_deref())
                .expect("local harness dir"),
            &["a", "b"],
        );
        crate::test_support::write_factory_specs(
            &pa_core::refinement::get_global_harness_state_dir(&agent_dir),
            &["shared"],
        );
        let graph = worker
            .dispatch("factory_activity", &serde_json::json!({ "action": "graph" }))
            .await;
        assert!(graph.success, "{graph:?}");
        assert_eq!(
            graph.data,
            Some(serde_json::json!({ "runs": [], "factorySpecs": 2 }))
        );
        let _ = worker.dispatch("kill", &serde_json::json!({})).await;
    }

    /// The opt-in default: no settings file, no factory lane.
    #[test]
    fn the_factory_lane_stays_unadvertised_until_enabled() {
        let dir = tempfile::TempDir::new().expect("temp dir");
        let agent_dir = dir.path().join("agent");
        std::fs::create_dir_all(&agent_dir).expect("agent dir");

        let disabled = advertised_server_capabilities(&agent_dir);
        assert!(
            !disabled
                .iter()
                .any(|capability| capability == "factory_activity"),
            "the default factory gate stays off: {disabled:?}"
        );

        // `/factory on` persists `factory.enabled` in the settings file —
        // the exact shared key the lane advertisement reads.
        let settings = serde_json::json!({ "factory": { "enabled": true } });
        std::fs::write(
            agent_dir.join("settings.json"),
            serde_json::to_string(&settings).expect("serialize"),
        )
        .expect("write settings");
        let enabled = advertised_server_capabilities(&agent_dir);
        assert!(
            enabled
                .iter()
                .any(|capability| capability == "factory_activity"),
            "the enabled factory advertises its lane: {enabled:?}"
        );

        // `/factory off` re-reads as disabled: the lane leaves the
        // advertisement again.
        let off = serde_json::json!({ "factory": { "enabled": false } });
        std::fs::write(
            agent_dir.join("settings.json"),
            serde_json::to_string(&off).expect("serialize"),
        )
        .expect("write settings");
        let disabled_again = advertised_server_capabilities(&agent_dir);
        assert!(
            !disabled_again
                .iter()
                .any(|capability| capability == "factory_activity"),
            "the disabled factory lane leaves the advertisement: {disabled_again:?}"
        );
    }
}
