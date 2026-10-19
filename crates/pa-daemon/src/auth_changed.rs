//! One-shot auth-change notification: refresh serving credentials in all
//! live session workers, including RLM children.

use pa_core::auth::PRIME_INFERENCE_PROVIDER_ID;
use serde_json::json;

use crate::backpressure::RouteAdmission;
use crate::protocol::{response_failure, response_success, DaemonResponse};
use crate::supervisor::{Supervisor, ROUTE_TIMEOUT_MS};

impl Supervisor {
    /// Notify every live connected worker (including RLM children) after a
    /// saved Prime credential changes. Workers that are not live yet build
    /// their target from the already-written store at spawn, so they are
    /// not counted. No credential material crosses this boundary.
    pub(crate) async fn handle_auth_changed_broadcast(
        &self,
        command_id: &str,
        type_name: &str,
        provider_id: &str,
    ) -> DaemonResponse {
        if provider_id != PRIME_INFERENCE_PROVIDER_ID {
            return response_failure(Some(command_id), type_name, "Unsupported provider", None);
        }
        let residents = self.live_workers_in_creation_order().await;
        let responses = futures::future::join_all(residents.iter().map(|resident| async move {
            self.route_command_typed(
                resident,
                "auth_changed",
                json!({ "providerId": provider_id }),
                ROUTE_TIMEOUT_MS,
                RouteAdmission::SupervisorInternal,
            )
            .await
        }))
        .await;
        let failures = responses
            .iter()
            .filter(|reply| !matches!(reply, Ok(response) if response.success))
            .count();
        if failures > 0 {
            return response_failure(
                Some(command_id),
                type_name,
                &format!("Could not update {failures} of {} running sessions. Shut down and restart Prime Agent to update running sessions.", residents.len()),
                None,
            );
        }
        response_success(Some(command_id), type_name, None)
    }
}
