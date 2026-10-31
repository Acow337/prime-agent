//! The engine unit battery: the scripted tool loop, the child-depth
//! stamp, the MCP-gating unlock, and the goal/heartbeat handler
//! registration.
use super::*;
use crate::session_engine::tool_bridge::{bridge_tool, ToolDefinitionBridge};
use crate::tools::tool_definition::{ExecutionMode, ToolDefinition, ToolExecutionResult};
use pa_agent::scripted::ScriptedProvider;

use crate::session_engine::treatment::ReplPersistence;

fn echo_definition() -> ToolDefinition {
    ToolDefinition {
        name: "echo".to_string(),
        label: "Echo".to_string(),
        description: "Echoes its input".to_string(),
        prompt_snippet: String::new(),
        parameters: serde_json::json!({
            "type": "object",
            "properties": { "text": { "type": "string" } },
            "required": ["text"]
        }),
        execution_mode: Some(ExecutionMode::Sequential),
        prepare_arguments: None,
        execute: Arc::new(|_id, params, _signal, _on_update| {
            let text = params
                .get("text")
                .and_then(|t| t.as_str())
                .unwrap_or_default()
                .to_string();
            Box::pin(async move { Ok(ToolExecutionResult::text(format!("echo: {text}"))) })
        }),
    }
}

#[tokio::test]
async fn engine_runs_tool_loop_and_persists() {
    let model = pa_agent::types::Model {
        id: "m".into(),
        name: "m".into(),
        api: "test".into(),
        provider: "test".into(),
        base_url: "http://localhost".into(),
        reasoning: false,
        cost: pa_agent::types::UsageCost::default(),
        context_window: 1_000,
        max_tokens: 100,
        max_tokens_explicit: false,
    };
    let provider = Arc::new(ScriptedProvider::new(model.clone()));
    provider.push_tool_call_turn(
        Some("checking"),
        vec![("call-1", "echo", serde_json::json!({ "text": "hi" }))],
    );
    provider.push_text_turn("all done");

    let tmp = tempfile::tempdir().unwrap();
    let cwd = tmp.path().join("project");
    std::fs::create_dir_all(&cwd).unwrap();
    let engine = create_session(SessionEngineConfig {
        on_late_sent_agent_message: None,
        semantic_edges: None,
        cron_store: None,
        queued_steering_probe: None,
        should_stop_after_turn: None,
        image_model_router: None,
        steering_mode: None,
        follow_up_mode: None,
        cwd: cwd.clone(),
        agent_dir: tmp.path().join("agent"),
        mcp_manager: None,
        model: Some(model),
        thinking_level: None,
        stream_fn: Some(provider.stream_fn()),
        tools: vec![bridge_tool(echo_definition())],
        custom_system_prompt: None,
        prompt_guidelines: vec![],
        generic_mcp_servers: vec![],
        allow_recursion: None,
        session_manager: None,
        extra_host_handlers: None,
        conversation_log_path: None,
        additional_skill_paths: vec![],
        additional_prompt_paths: vec![],
        extra_builtin_skill_overrides: vec![],
        rlm_subagent_host: None,
        rlm_usage_store: None,
        progress_note_emit: None,
        rlm_depth: None,
        daemonless: true,
        telemetry: None,
        model_info: None,
        on_background_work_settled: None,
        prewarm_ipython_kernel: None,
        queued_goal_context_purge: None,
    })
    .await
    .unwrap();

    // The system prompt is the layered assembly: static core layer
    // first, dynamic tail after.
    assert!(engine.system_prompt.starts_with("# prime-agent harness"));
    assert!(engine
        .system_prompt
        .contains("Recursive agent depth: 0 (root)"));

    let outcome = engine
        .prompt("run the echo tool", PromptOptions::default())
        .await
        .unwrap();
    assert_eq!(outcome, PromptOutcome::Prompt);
    engine.session.agent().wait_for_idle().await;

    let state = engine.session.agent().state().await;
    assert!(state.messages.iter().any(|message| match message {
        pa_agent::types::AgentMessage::Standard(pa_agent::types::Message::ToolResult(result)) => {
            result.tool_name == "echo"
        }
        _ => false,
    }));
    assert!(state.messages.iter().any(|message| match message {
        pa_agent::types::AgentMessage::Standard(pa_agent::types::Message::Assistant(assistant)) => {
            assistant.content.iter().any(|block| {
                matches!(
                    block,
                    pa_agent::types::AssistantContent::Text(text) if text.text == "all done"
                )
            })
        }
        _ => false,
    }));
    let entries = engine.session.entries().await;
    assert!(entries.iter().any(|entry| matches!(
        entry,
        pa_types::session::FileEntry::Message {
            message: pa_types::session::AgentMessage::User(user),
            ..
        } if user.content.text() == "run the echo tool"
    )));
    let _ = ToolDefinitionBridge::new;
}

/// A spawned child's prompt stamps its recursion depth: `create_session`
/// at depth N reads "depth: N (not root)", never the root identity.
#[tokio::test]
async fn spawned_child_prompt_stamps_its_depth() {
    let model = pa_agent::types::Model {
        id: "m".into(),
        name: "m".into(),
        api: "test".into(),
        provider: "test".into(),
        base_url: "http://localhost".into(),
        reasoning: false,
        cost: pa_agent::types::UsageCost::default(),
        context_window: 1_000,
        max_tokens: 100,
        max_tokens_explicit: false,
    };
    let provider = Arc::new(ScriptedProvider::new(model.clone()));
    let tmp = tempfile::tempdir().unwrap();
    let cwd = tmp.path().join("project");
    std::fs::create_dir_all(&cwd).unwrap();
    let engine = create_session(SessionEngineConfig {
        on_late_sent_agent_message: None,
        semantic_edges: None,
        cron_store: None,
        queued_steering_probe: None,
        should_stop_after_turn: None,
        image_model_router: None,
        steering_mode: None,
        follow_up_mode: None,
        cwd: cwd.clone(),
        agent_dir: tmp.path().join("agent"),
        mcp_manager: None,
        model: Some(model),
        thinking_level: None,
        stream_fn: Some(provider.stream_fn()),
        tools: vec![bridge_tool(echo_definition())],
        custom_system_prompt: None,
        prompt_guidelines: vec![],
        generic_mcp_servers: vec![],
        allow_recursion: None,
        session_manager: None,
        extra_host_handlers: None,
        conversation_log_path: None,
        additional_skill_paths: vec![],
        additional_prompt_paths: vec![],
        extra_builtin_skill_overrides: vec![],
        rlm_subagent_host: None,
        rlm_usage_store: None,
        progress_note_emit: None,
        rlm_depth: Some(2),
        daemonless: true,
        telemetry: None,
        model_info: None,
        on_background_work_settled: None,
        prewarm_ipython_kernel: None,
        queued_goal_context_purge: None,
    })
    .await
    .unwrap();

    assert!(engine
        .system_prompt
        .contains("Recursive agent depth: 2 (not root)"));
    assert!(!engine.system_prompt.contains("depth: 0 (root)"));
}

/// The login chain's prompt-gating end to end: a settings-declared OAuth
/// server stays gated, an endpoint-bound credential unlocks it in the NEXT
/// session, and one bound to another endpoint does not.
#[tokio::test]
async fn oauth_creds_unlock_generic_mcp_gating_in_new_sessions() {
    fn model() -> pa_agent::types::Model {
        pa_agent::types::Model {
            id: "m".into(),
            name: "m".into(),
            api: "test".into(),
            provider: "test".into(),
            base_url: "http://localhost".into(),
            reasoning: false,
            cost: pa_agent::types::UsageCost::default(),
            context_window: 1_000,
            max_tokens: 100,
            max_tokens_explicit: false,
        }
    }

    fn config(
        cwd: &std::path::Path,
        agent_dir: &std::path::Path,
        stream_fn: pa_agent::stream::StreamFn,
    ) -> SessionEngineConfig {
        SessionEngineConfig {
            on_late_sent_agent_message: None,
            semantic_edges: None,
            cron_store: None,
            queued_steering_probe: None,
            should_stop_after_turn: None,
            image_model_router: None,
            steering_mode: None,
            follow_up_mode: None,
            cwd: cwd.to_path_buf(),
            agent_dir: agent_dir.to_path_buf(),
            mcp_manager: None,
            model: Some(model()),
            thinking_level: None,
            stream_fn: Some(stream_fn),
            tools: vec![],
            custom_system_prompt: None,
            prompt_guidelines: vec![],
            generic_mcp_servers: vec![],
            allow_recursion: None,
            session_manager: None,
            extra_host_handlers: None,
            conversation_log_path: None,
            additional_skill_paths: vec![],
            additional_prompt_paths: vec![],
            extra_builtin_skill_overrides: vec![],
            rlm_subagent_host: None,
            rlm_usage_store: None,
            progress_note_emit: None,
            rlm_depth: None,
            daemonless: true,
            telemetry: None,
            model_info: None,
            on_background_work_settled: None,
            prewarm_ipython_kernel: None,
            queued_goal_context_purge: None,
        }
    }

    let tmp = tempfile::tempdir().unwrap();
    let cwd = tmp.path().join("project");
    let agent_dir = tmp.path().join("agent");
    std::fs::create_dir_all(&cwd).unwrap();
    std::fs::create_dir_all(&agent_dir).unwrap();
    // The settings declaration the daemon worker's MCP manager also
    // resolves (an OAuth HTTP server, like `mcp add ... --oauth`).
    std::fs::write(
        agent_dir.join("settings.json"),
        serde_json::json!({
            "mcpServers": {
                "fixture-oauth": {
                    "type": "http",
                    "url": "https://fixture.example/mcp",
                    "oauth": true,
                },
            },
        })
        .to_string(),
    )
    .unwrap();
    let provider = Arc::new(ScriptedProvider::new(model()));

    let engine = create_session(config(&cwd, &agent_dir, provider.stream_fn()))
        .await
        .unwrap();
    assert!(!engine.system_prompt.contains("# Generic MCP Connections"));

    // The persisted credential begin_login leaves behind (the TS
    // McpCredentials shape, endpoint-bound).
    let write_credential = |endpoint: &str| {
        std::fs::write(
            agent_dir.join("auth.json"),
            serde_json::json!({
                "mcp:fixture-oauth": {
                    "type": "oauth",
                    "access": "fixture-access",
                    "refresh": "fixture-refresh",
                    "expires": 999_999_999_999_999_i64,
                    "endpoint": endpoint,
                    "tokenEndpoint": "https://fixture.example/token",
                    "clientId": "fixture-client",
                },
            })
            .to_string(),
        )
        .unwrap();
    };

    // A credential bound to another endpoint stays gated: the token
    // must prove where it belongs.
    write_credential("https://other.example/mcp");
    let engine = create_session(config(&cwd, &agent_dir, provider.stream_fn()))
        .await
        .unwrap();
    assert!(!engine.system_prompt.contains("# Generic MCP Connections"));

    write_credential("https://fixture.example/mcp");
    let engine = create_session(config(&cwd, &agent_dir, provider.stream_fn()))
        .await
        .unwrap();
    assert!(engine.system_prompt.contains("# Generic MCP Connections"));
    assert!(engine.system_prompt.contains("`fixture-oauth`"));
}

#[tokio::test]
async fn create_session_registers_goal_and_heartbeat_handlers() {
    let dir = tempfile::TempDir::new().unwrap();
    let registration =
        pa_ai::faux::register_faux_provider(pa_ai::faux::RegisterFauxProviderOptions {
            models: Some(vec![pa_ai::faux::FauxModelDefinition {
                id: "faux-1".to_string(),
                name: Some("Faux".to_string()),
                reasoning: Some(false),
                input: Some(vec![pa_types::ai::ModelInput::Text]),
                cost: None,
                context_window: Some(100_000),
                max_tokens: Some(4_096),
            }]),
            ..Default::default()
        });
    registration.set_responses(vec![pa_ai::faux::FauxResponseStep::Message(
        pa_ai::faux::faux_assistant_text_message(
            "ok",
            pa_ai::faux::FauxAssistantMessageOptions::default(),
        ),
    )]);
    let model = registration.get_model();
    let agent_model = crate::session_engine::provider_adapter::json_round_trip(&model).unwrap();
    let stream_fn = crate::session_engine::provider_adapter::real_stream_fn(None, model.clone());
    let engine = create_session(SessionEngineConfig {
        cron_store: None,
        cwd: dir.path().to_path_buf(),
        agent_dir: dir.path().to_path_buf(),
        model: Some(agent_model),
        stream_fn: Some(stream_fn),
        tools: Vec::new(),
        ..Default::default()
    })
    .await
    .unwrap();
    let names: Vec<String> = engine
        .session
        .agent()
        .state()
        .await
        .tools
        .iter()
        .map(|tool| tool.name().to_string())
        .collect();
    assert!(
        names.iter().any(|name| name == "python_repl"),
        "tools: {names:?}"
    );
}

/// A daemon-shaped resume: an in-memory session manager (the daemon's
/// non-persisted engine session) already holding a message, plus a
/// `conversation_log_path`, still auto-appends the missing
/// thinking-level and service-tier rows (#3350's gate regression).
#[tokio::test]
async fn create_session_appends_missing_resume_settings_rows() {
    let model = pa_agent::types::Model {
        id: "m".into(),
        name: "m".into(),
        api: "test".into(),
        provider: "test".into(),
        base_url: "http://localhost".into(),
        reasoning: false,
        cost: pa_agent::types::UsageCost::default(),
        context_window: 1_000,
        max_tokens: 100,
        max_tokens_explicit: false,
    };
    let provider = Arc::new(ScriptedProvider::new(model.clone()));
    let tmp = tempfile::tempdir().unwrap();
    let cwd = tmp.path().join("project");
    std::fs::create_dir_all(&cwd).unwrap();
    let mut session_manager =
        crate::session::manager::SessionManager::in_memory_in_session_dir(&cwd, tmp.path());
    session_manager
        .append_message(pa_types::session::AgentMessage::User(
            pa_types::ai::UserMessage {
                content: pa_types::ai::UserContent::Text("resumed".into()),
                timestamp: 0,
                rest: serde_json::Map::default(),
            },
        ))
        .unwrap();
    let engine = create_session(SessionEngineConfig {
        on_late_sent_agent_message: None,
        semantic_edges: None,
        cron_store: None,
        queued_steering_probe: None,
        should_stop_after_turn: None,
        image_model_router: None,
        steering_mode: None,
        follow_up_mode: None,
        cwd: cwd.clone(),
        agent_dir: tmp.path().join("agent"),
        mcp_manager: None,
        model: Some(model),
        thinking_level: None,
        stream_fn: Some(provider.stream_fn()),
        tools: vec![],
        custom_system_prompt: None,
        prompt_guidelines: vec![],
        generic_mcp_servers: vec![],
        allow_recursion: None,
        session_manager: Some(session_manager),
        extra_host_handlers: None,
        conversation_log_path: Some(tmp.path().join("session.jsonl")),
        additional_skill_paths: vec![],
        additional_prompt_paths: vec![],
        extra_builtin_skill_overrides: vec![],
        rlm_subagent_host: None,
        rlm_usage_store: None,
        progress_note_emit: None,
        rlm_depth: None,
        daemonless: true,
        telemetry: None,
        model_info: None,
        on_background_work_settled: None,
        prewarm_ipython_kernel: None,
        queued_goal_context_purge: None,
    })
    .await
    .unwrap();

    let entries = engine.session.entries().await;
    assert!(
        entries.iter().any(|entry| matches!(
            entry,
            pa_types::session::FileEntry::ThinkingLevelChange { .. }
        )),
        "entries: {entries:?}"
    );
    assert!(
        entries.iter().any(|entry| matches!(
            entry,
            pa_types::session::FileEntry::ServiceTierChange { .. }
        )),
        "entries: {entries:?}"
    );
}

/// The R treatment test needs a live kernel; skip when the ambient
/// environment has none (the same contract the kernel integration tests use).
fn treatment_kernel_available() -> bool {
    if let Some(explicit) = std::env::var_os("PRIME_AGENT_KERNEL_PYTHON") {
        return std::path::Path::new(&explicit).exists();
    }
    let fallback = std::path::PathBuf::from(std::env::var("HOME").map_or_else(
        |_| "/home/ubuntu/.prime/agent/kernel-venv/bin/python".to_string(),
        |home| format!("{home}/.prime/agent/kernel-venv/bin/python"),
    ));
    fallback.exists()
}

fn treatment_model() -> pa_agent::types::Model {
    pa_agent::types::Model {
        id: "treatment-model".into(),
        name: "Treatment model".into(),
        api: "test".into(),
        provider: "test".into(),
        base_url: "http://localhost".into(),
        reasoning: false,
        cost: pa_agent::types::UsageCost::default(),
        context_window: 16_000,
        max_tokens: 1_000,
        max_tokens_explicit: false,
    }
}

async fn treatment_engine(
    treatment: super::super::treatment::HarnessTreatment,
    provider: &std::sync::Arc<ScriptedProvider>,
    host: Option<std::sync::Arc<dyn super::super::rlm_host::RlmSubagentHost>>,
) -> (tempfile::TempDir, SessionEngine) {
    let tmp = tempfile::tempdir().unwrap();
    let cwd = tmp.path().join("project");
    std::fs::create_dir_all(&cwd).unwrap();
    let engine = create_session_with_treatment(
        SessionEngineConfig {
            cwd,
            agent_dir: tmp.path().join("agent"),
            model: Some(treatment_model()),
            stream_fn: Some(provider.stream_fn()),
            rlm_subagent_host: host,
            daemonless: true,
            ..SessionEngineConfig::default()
        },
        treatment,
    )
    .await
    .unwrap();
    (tmp, engine)
}

fn tool_text(messages: &[pa_agent::types::AgentMessage], tool_call_id: &str) -> String {
    messages
        .iter()
        .find_map(|message| match message {
            pa_agent::types::AgentMessage::Standard(pa_agent::types::Message::ToolResult(
                result,
            )) if result.tool_call_id == tool_call_id => Some(
                result
                    .content
                    .iter()
                    .filter_map(|content| match content {
                        pa_agent::types::ToolResultContent::Text(text) => Some(text.text.as_str()),
                        pa_agent::types::ToolResultContent::Image(_) => None,
                    })
                    .collect::<Vec<_>>()
                    .join("\n"),
            ),
            _ => None,
        })
        .unwrap_or_default()
}

async fn run_two_python_turns(repl: super::super::treatment::ReplPersistence) -> String {
    let provider = std::sync::Arc::new(ScriptedProvider::new(treatment_model()));
    provider.push_tool_call_turn(
        None,
        vec![(
            "set-value",
            "python_repl",
            serde_json::json!({ "code": "cross_turn_value = 17" }),
        )],
    );
    provider.push_tool_call_turn(
        None,
        vec![(
            "get-value",
            "python_repl",
            serde_json::json!({
                "code": "globals().get('cross_turn_value', 'missing')"
            }),
        )],
    );
    provider.push_text_turn("done");
    let (_tmp, engine) = treatment_engine(
        super::super::treatment::HarnessTreatment {
            repl,
            ..super::super::treatment::HarnessTreatment::FULL
        },
        &provider,
        None,
    )
    .await;
    engine
        .prompt("exercise the two cells", PromptOptions::default())
        .await
        .unwrap();
    engine.session.agent().wait_for_idle().await;
    let state = engine.session.agent().state().await;
    tool_text(&state.messages, "get-value")
}

#[tokio::test]
async fn repl_treatment_resets_only_between_model_turns() {
    if !treatment_kernel_available() {
        eprintln!("no kernel python; skipping the REPL treatment test");
        return;
    }
    let persistent = run_two_python_turns(ReplPersistence::AcrossTurns).await;
    let stateless = run_two_python_turns(ReplPersistence::ResetAfterTurn).await;
    assert!(persistent.contains("17"), "persistent: {persistent}");
    assert!(stateless.contains("missing"), "stateless: {stateless}");
}

/// A host that must never be reached when S-off removes the spawn surface.
#[derive(Default)]
struct CountingHost {
    spawns: std::sync::atomic::AtomicUsize,
}

impl super::super::rlm_host::RlmSubagentHost for CountingHost {
    fn spawn(
        &self,
        _request: super::super::rlm_host::RlmSpawnRequest,
    ) -> super::super::rlm_host::RlmHostFuture<super::super::rlm_host::RlmSpawnHandle> {
        self.spawns
            .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        Box::pin(async { anyhow::bail!("counting host must not be called") })
    }

    fn create_session(
        &self,
        _request: super::super::rlm_host::RlmCreateSessionRequest,
    ) -> super::super::rlm_host::RlmHostFuture<super::super::rlm_host::RlmCreateSessionHandle> {
        Box::pin(async { anyhow::bail!("counting host must not be called") })
    }

    fn list_subagents(
        &self,
    ) -> super::super::rlm_host::RlmHostFuture<Vec<super::super::rlm_host::RlmSubagentEntry>> {
        Box::pin(async { Ok(Vec::new()) })
    }

    fn delete_subagent(
        &self,
        target: String,
    ) -> super::super::rlm_host::RlmHostFuture<super::super::rlm_host::RlmDeleteSubagentResult>
    {
        Box::pin(async move { anyhow::bail!("unexpected delete for {target}") })
    }

    fn collect(
        &self,
        _targets: Vec<String>,
        _timeout_ms: u64,
    ) -> super::super::rlm_host::RlmHostFuture<Vec<super::super::rlm_host::RlmChildResult>> {
        Box::pin(async { Ok(Vec::new()) })
    }

    fn rename(
        &self,
        name: String,
        _session_id: Option<String>,
    ) -> super::super::rlm_host::RlmHostFuture<String> {
        Box::pin(async move { Ok(name) })
    }
}

#[tokio::test]
async fn subagent_treatment_removes_spawn_surface_and_never_calls_host() {
    if !treatment_kernel_available() {
        eprintln!("no kernel python; skipping the subagent treatment test");
        return;
    }
    let host = std::sync::Arc::new(CountingHost::default());
    let provider = std::sync::Arc::new(ScriptedProvider::new(treatment_model()));
    let (_tmp, engine) = treatment_engine(
        super::super::treatment::HarnessTreatment {
            subagents: super::super::treatment::SubagentAccess::Disabled,
            ..super::super::treatment::HarnessTreatment::FULL
        },
        &provider,
        Some(host.clone()),
    )
    .await;

    assert!(engine
        .system_prompt
        .contains("Subagent spawning is disabled in this session."));
    let manager = engine.provisioner.ensure(None, None).await.unwrap();
    let result = manager
        .execute(
            "import rlm\nawait rlm.spawn('work', name='worker')",
            crate::kernel::shared::ExecuteOptions::default(),
        )
        .await
        .unwrap();
    assert_eq!(result.status, crate::kernel::shared::ExecuteStatus::Error);
    assert!(result
        .error
        .as_ref()
        .is_some_and(|error| error.evalue.contains("no RLM child runtime")));
    assert_eq!(host.spawns.load(std::sync::atomic::Ordering::SeqCst), 0);
}

#[tokio::test]
async fn experiment_harness_treatment_pins_the_isolated_agent_dir() {
    if !treatment_kernel_available() {
        eprintln!("no kernel python; skipping the harness treatment test");
        return;
    }
    let provider = std::sync::Arc::new(ScriptedProvider::new(treatment_model()));
    let (tmp, engine) = treatment_engine(
        super::super::treatment::HarnessTreatment {
            harness_state: super::super::treatment::HarnessStateAccess::Disabled,
            ..super::super::treatment::HarnessTreatment::FULL
        },
        &provider,
        None,
    )
    .await;

    let settings = crate::settings::SettingsManager::create(
        tmp.path().join("project"),
        tmp.path().join("agent"),
    );
    assert!(!settings.get_harness_refinements());
    assert!(!engine.session.harness_refinements_enabled());
    assert!(engine.session.harness_digest_inputs().await.is_none());
    assert!(!engine.session.auto_refine_allowed());
    assert!(!engine.skills.iter().any(|skill| skill.name == "refine"));
    let manager = engine.provisioner.ensure(None, None).await.unwrap();
    let result = manager
        .execute(
            "import rlm\nrlm.harness.create_memory('x', 'y')",
            crate::kernel::shared::ExecuteOptions::default(),
        )
        .await
        .unwrap();
    assert_eq!(result.status, crate::kernel::shared::ExecuteStatus::Error);
    assert!(result
        .error
        .as_ref()
        .is_some_and(|error| error.evalue.contains("disabled in /settings")));
}

#[tokio::test]
async fn normal_create_session_preserves_the_users_harness_setting() {
    let tmp = tempfile::tempdir().unwrap();
    let cwd = tmp.path().join("project");
    let agent_dir = tmp.path().join("agent");
    std::fs::create_dir_all(&cwd).unwrap();
    let mut settings = crate::settings::SettingsManager::create(&cwd, &agent_dir);
    settings.set_harness_refinements(false).unwrap();
    let provider = std::sync::Arc::new(ScriptedProvider::new(treatment_model()));
    let _engine = create_session(SessionEngineConfig {
        cwd: cwd.clone(),
        agent_dir: agent_dir.clone(),
        model: Some(treatment_model()),
        stream_fn: Some(provider.stream_fn()),
        daemonless: true,
        ..SessionEngineConfig::default()
    })
    .await
    .unwrap();
    assert!(!crate::settings::SettingsManager::create(&cwd, &agent_dir).get_harness_refinements());
}

#[tokio::test]
async fn every_harness_treatment_matches_its_factor_id_and_surfaces() {
    if !treatment_kernel_available() {
        eprintln!("no kernel python; skipping the treatment matrix test");
        return;
    }
    for repl in [
        ReplPersistence::AcrossTurns,
        ReplPersistence::ResetAfterTurn,
    ] {
        for subagents in [
            super::super::treatment::SubagentAccess::Enabled,
            super::super::treatment::SubagentAccess::Disabled,
        ] {
            for harness_state in [
                super::super::treatment::HarnessStateAccess::Enabled,
                super::super::treatment::HarnessStateAccess::Disabled,
            ] {
                let treatment = super::super::treatment::HarnessTreatment {
                    repl,
                    subagents,
                    harness_state,
                };
                let provider = std::sync::Arc::new(ScriptedProvider::new(treatment_model()));
                let host = std::sync::Arc::new(CountingHost::default());
                let (_tmp, engine) = treatment_engine(treatment, &provider, Some(host)).await;
                assert_eq!(engine.harness_treatment, treatment, "{}", treatment.id());
                assert_eq!(
                    engine
                        .system_prompt
                        .contains("Subagent spawning is disabled in this session."),
                    subagents == super::super::treatment::SubagentAccess::Disabled,
                    "{}",
                    treatment.id(),
                );
                assert_eq!(
                    engine.session.harness_refinements_enabled(),
                    harness_state == super::super::treatment::HarnessStateAccess::Enabled,
                    "{}",
                    treatment.id(),
                );
            }
        }
    }
}
