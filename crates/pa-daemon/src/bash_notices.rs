//! The daemon worker's kernel-notice seams (bash completions and factory
//! progress): the notice types, the validation, and the handler
//! registration live in pa-core (`session_engine::bash_notices`); the
//! worker keeps its queue-lane sinks here.

use pa_core::kernel::shared::HostRequestHandlers;

use crate::agent_engine::AgentSessionEngine;

impl AgentSessionEngine {
    /// Wire the worker's kernel-notice queue seams (called once at
    /// construction, before the first session build reads them).
    ///
    /// # Panics
    ///
    /// Panics when a sink mutex is poisoned (a holder panicked while
    /// holding the notice or consumed-sink lock).
    pub fn set_kernel_notice_sinks(
        &self,
        notice: crate::engine::KernelNoticeSink,
        consumed: crate::engine::BashConsumedSink,
    ) {
        *self
            .kernel_notice_sink
            .lock()
            .expect("kernel notice sink lock") = Some(notice);
        *self
            .bash_consumed_sink
            .lock()
            .expect("bash consumed sink lock") = Some(consumed);
    }

    /// The `bash.completed`/`bash.consumed`/`factory.progress` kernel host
    /// handlers. Registered only when both seams are wired — no worker queue
    /// leaves the requests honestly unavailable.
    pub(crate) fn register_kernel_notice_host_handlers(&self, handlers: &mut HostRequestHandlers) {
        let Some(notice) = self
            .kernel_notice_sink
            .lock()
            .expect("kernel notice sink lock")
            .clone()
        else {
            return;
        };
        let Some(consumed) = self
            .bash_consumed_sink
            .lock()
            .expect("bash consumed sink lock")
            .clone()
        else {
            return;
        };
        pa_core::session_engine::bash_notices::register_kernel_notice_host_handlers(
            notice, consumed, handlers,
        );
    }
}

/// Map one promoted inline bash job's completion onto the kernel notice
/// lane's shape (`[bash-done pid:N exit:M]`): the same `BashCompletionNotice`
/// the `bash.completed` host handler validates and admits. `None` when the
/// promoted job has no pid (the operations backend did not report one).
pub(crate) fn auto_background_to_bash_completion(
    completion: &pa_agent::auto_background::AutoBackgroundCompletion,
) -> Option<pa_core::session_engine::bash_notices::BashCompletionNotice> {
    let pid = completion.pid?;
    Some(
        pa_core::session_engine::bash_notices::BashCompletionNotice {
            pid,
            command: completion.command.clone(),
            exit_code: i64::from(completion.exit_code.unwrap_or(-1)),
        },
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn promoted_completions_map_onto_the_bash_done_lane() {
        let completion = pa_agent::auto_background::AutoBackgroundCompletion {
            pid: Some(4321),
            exit_code: Some(0),
            command: "sleep 61".to_string(),
            output: String::new(),
            full_output_path: None,
        };
        let notice = auto_background_to_bash_completion(&completion).expect("pid known");
        // The promoted job rides the exact existing bash-done shape: the
        // row the worker admits for kernel background completions.
        let row = pa_core::session_engine::messages::create_async_bash_completion_message(
            notice.pid,
            &notice.command,
            notice.exit_code,
            1_000,
        );
        assert_eq!(
            row.content.text().as_str(),
            "[bash-done pid:4321 exit:0]\n\nCommand: \"sleep 61\""
        );
        assert!(auto_background_to_bash_completion(
            &pa_agent::auto_background::AutoBackgroundCompletion {
                pid: None,
                exit_code: None,
                command: String::new(),
                output: String::new(),
                full_output_path: None,
            }
        )
        .is_none());
    }
}
