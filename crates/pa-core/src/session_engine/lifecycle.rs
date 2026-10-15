//! Cancellable lifecycle decisions, deferred until real session work starts.

use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use pa_agent::abort::AbortSignal;
use pa_agent::types::{
    AgentMessage, AssistantContent, Message, UserContent, UserMessage, UserPart,
};
use serde_json::{json, Value};

use super::hooks::Identity;

pub(super) struct LifecycleHooks {
    runtime: Arc<crate::hooks::HookRuntime>,
    identity: Arc<Identity>,
    pending_start: AtomicU8,
    deferred_rows: OnceLock<Arc<Mutex<Vec<pa_types::session::CustomMessage>>>>,
}

impl LifecycleHooks {
    pub fn might_run_precompact(&self) -> bool {
        self.runtime.might_run("PreCompact")
    }
    pub fn new(runtime: Arc<crate::hooks::HookRuntime>, identity: Arc<Identity>) -> Self {
        Self {
            runtime,
            identity,
            pending_start: AtomicU8::new(1),
            deferred_rows: OnceLock::new(),
        }
    }

    pub fn set_deferred_rows(&self, rows: Arc<Mutex<Vec<pa_types::session::CustomMessage>>>) {
        let _ = self.deferred_rows.set(rows);
    }

    pub fn mark_resume_from_history(&self, messages: &[pa_types::session::AgentMessage]) {
        // Host setup and injected goal/custom rows can precede the first real
        // prompt. Only durable conversation (including compacted summaries)
        // establishes a resumed session.
        if !messages
            .iter()
            .any(|message| !matches!(message, pa_types::session::AgentMessage::Custom(_)))
        {
            return;
        }
        // Daemon hosts adopt durable history after engine construction. Only
        // classify the initial pending start: navigation must not rearm it,
        // nor replace a pending post-compaction start.
        let _ = self
            .pending_start
            .compare_exchange(1, 2, Ordering::AcqRel, Ordering::Acquire);
    }

    pub fn mark_compact(&self) {
        self.pending_start.store(3, Ordering::Release);
    }

    fn common(&self, event: &str, model: &str) -> Value {
        json!({
            "session_id": self.identity.session_id,
            "cwd": self.identity.cwd,
            "transcript_path": self.identity.transcript_path,
            "hook_event_name": event,
            "model": model,
            "permission_mode": "default",
        })
    }

    pub async fn start(&self, model: &str, signal: AbortSignal) -> anyhow::Result<()> {
        let pending = self.pending_start.load(Ordering::Acquire);
        let source = match pending {
            1 => "startup",
            2 => "resume",
            3 => "compact",
            _ => return Ok(()),
        };
        let mut input = self.common("SessionStart", model);
        input["source"] = json!(source);
        let outcome = self.runtime.dispatch(input, signal.clone()).await;
        pa_agent::abort::throw_if_aborted_signal(Some(&signal))?;
        // A completed lifecycle decision is one-shot even when it stops this
        // admission. Cancellation leaves the pending event available to retry.
        let _ =
            self.pending_start
                .compare_exchange(pending, 0, Ordering::AcqRel, Ordering::Acquire);
        if let Some(reason) = outcome.stop.or(outcome.deny) {
            anyhow::bail!("{reason}");
        }
        if let Some(message) = outcome.message {
            tracing::warn!(%message, "session-start hook message");
        }
        Ok(())
    }

    pub fn input(
        self: Arc<Self>,
        messages: Vec<AgentMessage>,
        model: String,
        signal: AbortSignal,
    ) -> impl std::future::Future<Output = anyhow::Result<()>> + Send {
        // Admission already took these rows from the deferred mailbox. Restore
        // them on veto, cancellation or dropped detached admission, ahead of any
        // notices that arrived while commands were running.
        let mut deferred = DeferredRows {
            mailbox: self.deferred_rows.get().cloned(),
            rows: messages
                .iter()
                .filter(|message| matches!(message, AgentMessage::Custom(_)))
                .filter_map(|message| match super::loop_message_to_session(message) {
                    Some(pa_types::session::AgentMessage::Custom(row)) => Some(row),
                    _ => None,
                })
                .collect(),
        };
        async move {
            self.start(&model, signal.clone()).await?;
            if !self.runtime.might_run("UserPromptSubmit") {
                deferred.rows.clear();
                return Ok(());
            }
            for message in messages {
                let AgentMessage::Standard(Message::User(user)) = message else {
                    continue;
                };
                let prompt = match user.content {
                    UserContent::Text(text) => text,
                    UserContent::Parts(parts) => parts
                        .into_iter()
                        .filter_map(|part| {
                            if let UserPart::Text(text) = part {
                                Some(text.text)
                            } else {
                                None
                            }
                        })
                        .collect::<Vec<_>>()
                        .join("\n"),
                };
                let mut input = self.common("UserPromptSubmit", &model);
                input["turn_id"] = json!(*self
                    .identity
                    .run_id
                    .read()
                    .unwrap_or_else(std::sync::PoisonError::into_inner));
                input["prompt"] = json!(prompt);
                let outcome = self.runtime.dispatch(input, signal.clone()).await;
                pa_agent::abort::throw_if_aborted_signal(Some(&signal))?;
                if let Some(reason) = outcome.stop.or(outcome.deny) {
                    anyhow::bail!("{reason}");
                }
                if let Some(message) = outcome.message {
                    tracing::warn!(%message, "prompt hook message");
                }
            }
            deferred.rows.clear();
            Ok(())
        }
    }

    pub async fn stop(
        &self,
        message: pa_agent::types::AssistantMessage,
        active: bool,
        signal: AbortSignal,
    ) -> anyhow::Result<Vec<AgentMessage>> {
        if !self.runtime.might_run("Stop") {
            return Ok(Vec::new());
        }
        let text = message
            .content
            .iter()
            .filter_map(|part| {
                if let AssistantContent::Text(text) = part {
                    Some(text.text.as_str())
                } else {
                    None
                }
            })
            .collect::<Vec<_>>()
            .join("");
        let mut input = self.common("Stop", &message.model);
        input["turn_id"] = json!(*self
            .identity
            .run_id
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner));
        input["stop_hook_active"] = json!(active);
        input["last_assistant_message"] = if text.is_empty() {
            Value::Null
        } else {
            json!(text)
        };
        let outcome = self.runtime.dispatch(input, signal.clone()).await;
        pa_agent::abort::throw_if_aborted_signal(Some(&signal))?;
        if let Some(message) = outcome.message {
            tracing::warn!(%message, "stop hook message");
        }
        if outcome.stop.is_some() {
            return Ok(Vec::new());
        }
        let Some(reason) = outcome.continuation else {
            return Ok(Vec::new());
        };
        if active {
            tracing::warn!(
                "Stop hook continuation ignored: one continuation per run limit reached"
            );
            return Ok(Vec::new());
        }
        Ok(vec![AgentMessage::Standard(Message::User(UserMessage {
            content: UserContent::Text(reason),
            timestamp: super::now_millis() as i64,
        }))])
    }
}

struct DeferredRows {
    mailbox: Option<Arc<Mutex<Vec<pa_types::session::CustomMessage>>>>,
    rows: Vec<pa_types::session::CustomMessage>,
}

impl Drop for DeferredRows {
    fn drop(&mut self) {
        if let Some(mailbox) = &self.mailbox {
            mailbox
                .lock()
                .unwrap_or_else(std::sync::PoisonError::into_inner)
                .splice(0..0, self.rows.drain(..));
        }
    }
}

impl super::AgentSession {
    pub(crate) async fn before_compaction_hook(
        &self,
        trigger: super::compact_session::CompactionTrigger,
        _custom_instructions: Option<&str>,
        model: &pa_types::ai::Model,
        abort: Option<&AbortSignal>,
    ) -> anyhow::Result<()> {
        let Some(hooks) = &self.lifecycle_hooks else {
            return Ok(());
        };
        if !hooks.runtime.might_run("PreCompact") {
            return Ok(());
        }
        let mut input = hooks.common("PreCompact", &model.id);
        input
            .as_object_mut()
            .expect("hook input object")
            .remove("permission_mode");
        input["turn_id"] = json!(*hooks
            .identity
            .run_id
            .read()
            .unwrap_or_else(std::sync::PoisonError::into_inner));
        input["trigger"] = json!(match trigger {
            super::compact_session::CompactionTrigger::Manual => "manual",
            super::compact_session::CompactionTrigger::Auto => "auto",
        });
        let signal = abort.cloned().unwrap_or_default();
        let outcome = hooks.runtime.dispatch(input, signal.clone()).await;
        pa_agent::abort::throw_if_aborted_signal(Some(&signal))?;
        if let Some(reason) = outcome.stop.or(outcome.deny) {
            anyhow::bail!("{reason}");
        }
        if let Some(message) = outcome.message {
            tracing::warn!(%message, "pre-compaction hook message");
        }
        Ok(())
    }

    pub(crate) fn mark_compaction_hook(&self) {
        if let Some(hooks) = &self.lifecycle_hooks {
            hooks.mark_compact();
        }
    }
}
