//! The session's live-input reload (`/reload`'s daemon half, and the
//! saved-auth change notification): re-read the request auth from the
//! credential store and the MCP manager's settings and auth state — the
//! pieces another process (a `/login`, a `/mcp` key flow) can change under
//! a running session.
use super::AgentSessionEngine;
use pa_core::session_engine::provider_adapter::ProviderTarget;

impl AgentSessionEngine {
    /// The one rebind rule for every cached target (the live slot, an
    /// armed image route, and the route's saved session target): the
    /// target serves exactly the pair a fresh resolution yields for its
    /// OWN model — the stored credential, the environment/runtime
    /// override, the configured `models.json` key, or none. Only the key
    /// and headers change, together, so a credential without a team
    /// header clears the previous `X-Prime-Team-ID`.
    ///
    /// The rule is applied even when the credential has left the store:
    /// a logout must stop serving the removed key, and every running
    /// session then serves what a newly spawned worker would. Callers
    /// gate the walk on a readable store, so a failed read never touches
    /// a target.
    fn rebind_request_auth(&self, target: &mut ProviderTarget) {
        let (api_key, headers) = self.resolve_request_key_and_headers(&target.model);
        target.api_key = api_key;
        target.headers = headers;
    }

    /// `/reload`'s session half, also run for a saved-auth change
    /// notification: re-read the live inputs another process may have
    /// changed — the MCP manager's settings and auth state, then the
    /// request auth of every cached target under
    /// [`Self::rebind_request_auth`]'s rule (the model and service tier
    /// never change). A failed auth-store read fails the call before any
    /// target is touched.
    pub(crate) fn reload_live_inputs(&self) -> Result<(), String> {
        // The whole refresh is serialized against every other
        // resolve-and-install (the session's reload lock): overlapping
        // writers resolve the store at their own read times, so the last
        // writer to run must be the one leaving the newest store.
        let _serialized = self
            .reload_lock
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        // The auth store re-read gates the rebinds below: the settings
        // re-read still applies on a failed reload.
        {
            let mut manager = self
                .mcp
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            let auth_reload = manager.reload_auth_storage();
            manager.refresh();
            auth_reload?;
        }
        // The armed route refreshes FIRST, the live slot SECOND: the
        // route lock fences the route's clone-and-install against this
        // refresh order (an attempt either installs the refreshed route
        // or lands before the slot refresh — never over it), and the
        // route's saved session target is the fallback a later
        // `clear_image_route` restores when resolution fails, so its auth
        // re-binds from the same store read.
        if let Some(route) = self
            .image_route
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .as_mut()
        {
            self.rebind_request_auth(&mut route.target);
            if let Some(saved) = route.session_target.as_mut() {
                self.rebind_request_auth(saved);
            }
        }
        // The live slot resolves outside its write lock (the resolution
        // can OAuth-refresh); a target that moved under the read (a
        // switch or route swap) re-resolves for whatever is live NOW, and
        // a cleared slot stays cleared.
        let live_model = self
            .provider_target
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .as_ref()
            .map(|target| target.model.clone());
        if let Some(model) = live_model {
            let (api_key, headers) = self.resolve_request_key_and_headers(&model);
            let mut slot = self
                .provider_target
                .write()
                .unwrap_or_else(std::sync::PoisonError::into_inner);
            if let Some(target) = slot.as_mut() {
                if target.model == model {
                    target.api_key = api_key;
                    target.headers = headers;
                } else {
                    self.rebind_request_auth(target);
                }
            }
        }
        Ok(())
    }
}
