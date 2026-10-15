use std::path::{Path, PathBuf};
use std::sync::Arc;

use pa_agent::scripted::ScriptedProvider;
use pa_agent::types::Model;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use super::engine::{create_session, SessionEngineConfig};
use super::{PromptOptions, SessionAgentMessage};

struct Fixture {
    _directory: tempfile::TempDir,
    cwd: PathBuf,
    agent_dir: PathBuf,
}

impl Fixture {
    fn new(document: &Value) -> Self {
        let directory = tempfile::tempdir().unwrap();
        let cwd = directory.path().join("project");
        let agent_dir = directory.path().join("agent");
        std::fs::create_dir(&cwd).unwrap();
        std::fs::create_dir(&agent_dir).unwrap();
        let source = agent_dir.join("hooks.json");
        let bytes = serde_json::to_vec(document).unwrap();
        std::fs::write(&source, &bytes).unwrap();
        let mut digest = Sha256::new();
        digest.update(
            source
                .canonicalize()
                .unwrap()
                .as_os_str()
                .as_encoded_bytes(),
        );
        digest.update([0]);
        digest.update(cwd.canonicalize().unwrap().as_os_str().as_encoded_bytes());
        digest.update([0]);
        digest.update(bytes);
        std::fs::write(
            agent_dir.join("hook-trust.json"),
            serde_json::to_vec(&json!({
                "version": 1, "trusted": [format!("{:x}", digest.finalize())]
            }))
            .unwrap(),
        )
        .unwrap();
        Self {
            _directory: directory,
            cwd,
            agent_dir,
        }
    }

    fn config(&self, provider: &Arc<ScriptedProvider>) -> SessionEngineConfig {
        SessionEngineConfig {
            cwd: self.cwd.clone(),
            agent_dir: self.agent_dir.clone(),
            model: Some(Model::unknown()),
            stream_fn: Some(provider.stream_fn()),
            custom_system_prompt: Some("Test lifecycle hooks.".into()),
            tools: vec![super::tool_bridge::bridge_tool(
                crate::tools::tool_definition::ToolDefinition {
                    name: "echo".into(),
                    label: "Echo".into(),
                    description: "Echo test".into(),
                    prompt_snippet: String::new(),
                    parameters: json!({"type": "object"}),
                    execution_mode: None,
                    prepare_arguments: None,
                    execute: Arc::new(|_, _, _, _| {
                        Box::pin(async {
                            Ok(crate::tools::tool_definition::ToolExecutionResult::text(
                                "echo result",
                            ))
                        })
                    }),
                },
            )],
            prewarm_ipython_kernel: Some(false),
            ..Default::default()
        }
    }
}

fn registry_model() -> pa_types::ai::Model {
    serde_json::from_value(json!({
        "id": "unknown", "name": "unknown", "api": "test", "provider": "test",
        "baseUrl": "", "reasoning": false, "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 8192, "maxTokens": 1024
    }))
    .unwrap()
}

fn events(path: &Path) -> Vec<Value> {
    std::fs::read_to_string(path)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

#[tokio::test]
async fn session_start_is_deferred_and_rearms_only_after_compaction() {
    let _command_guard = crate::hooks::test_command_guard().await;
    let fixture = Fixture::new(&json!({"hooks": {"SessionStart": [{"hooks": [{
        "type": "command", "command": "{ cat; printf '\\n'; } >> starts.jsonl"
    }]}]}}));
    let provider = Arc::new(ScriptedProvider::new(Model::unknown()));
    for text in ["one", "two", "three"] {
        provider.push_text_turn(text);
    }
    let engine = create_session(fixture.config(&provider)).await.unwrap();
    let path = fixture.cwd.join("starts.jsonl");
    assert!(
        !path.exists(),
        "session construction must not execute commands"
    );
    engine
        .prompt("first", PromptOptions::default())
        .await
        .unwrap();
    engine
        .prompt("second", PromptOptions::default())
        .await
        .unwrap();
    engine.session.mark_compaction_hook();
    engine
        .prompt("third", PromptOptions::default())
        .await
        .unwrap();
    let starts = events(&path);
    assert_eq!(
        starts
            .iter()
            .map(|event| event["source"].clone())
            .collect::<Vec<_>>(),
        vec![json!("startup"), json!("compact")]
    );
    assert!(starts.iter().all(|event| event.get("turn_id").is_none()));
    assert_eq!(provider.calls().len(), 3);
}

#[tokio::test]
async fn denied_prompt_restores_deferred_custom_rows_for_the_next_accepted_turn() {
    let _command_guard = crate::hooks::test_command_guard().await;
    let fixture = Fixture::new(&json!({"hooks": {"UserPromptSubmit": [{"hooks": [{
        "type": "command", "command": "cat >/dev/null; if [ ! -f allow ]; then printf '%s' '{\"decision\":\"block\",\"reason\":\"not yet\"}'; fi"
    }]}]}}));
    let provider = Arc::new(ScriptedProvider::new(Model::unknown()));
    provider.push_text_turn("accepted");
    let engine = create_session(fixture.config(&provider)).await.unwrap();
    let deferred = pa_types::session::CustomMessage {
        custom_type: "deferred-test".into(),
        content: pa_types::ai::UserContent::Text("preserve me".into()),
        display: false,
        details: None,
        timestamp: 17,
        rest: serde_json::Map::default(),
    };
    engine.session.queue_next_turn_row(deferred.clone());
    assert!(engine
        .prompt("denied", PromptOptions::default())
        .await
        .is_err());
    assert!(provider.calls().is_empty());
    std::fs::write(fixture.cwd.join("allow"), []).unwrap();
    engine
        .prompt("accepted", PromptOptions::default())
        .await
        .unwrap();
    let restored = engine.session.agent().state().await.messages.into_iter()
        .filter_map(|message| super::loop_message_to_session(&message))
        .filter(|message| matches!(message, SessionAgentMessage::Custom(row) if row.custom_type == "deferred-test"))
        .collect::<Vec<_>>();
    assert_eq!(restored, vec![SessionAgentMessage::Custom(deferred)]);
}

#[tokio::test]
async fn subagent_excludes_every_lifecycle_hook_even_with_shared_trusted_configuration() {
    let _command_guard = crate::hooks::test_command_guard().await;
    let mut hook_map = serde_json::Map::new();
    for event in [
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
        "SessionEnd",
        "PreCompact",
    ] {
        hook_map.insert(event.into(), json!([{"hooks": [{"type": "command",
            "command": "{ cat; printf '\\n'; } >> observed.jsonl", "async": event == "PostToolUse"}]}]));
    }
    let fixture = Fixture::new(&json!({"hooks": hook_map}));
    let parent_provider = Arc::new(ScriptedProvider::new(Model::unknown()));
    parent_provider.push_text_turn("parent");
    let parent = create_session(fixture.config(&parent_provider))
        .await
        .unwrap();
    parent
        .prompt("parent input", PromptOptions::default())
        .await
        .unwrap();
    parent
        .session
        .before_compaction_hook(
            super::compact_session::CompactionTrigger::Manual,
            None,
            &registry_model(),
            None,
        )
        .await
        .unwrap();
    parent.session_end_hook().unwrap()().await;
    let path = fixture.cwd.join("observed.jsonl");
    let baseline = std::fs::read(&path).unwrap();
    let child_provider = Arc::new(ScriptedProvider::new(Model::unknown()));
    child_provider.push_tool_call_turn(None, vec![("child-call", "echo", json!({}))]);
    child_provider.push_text_turn("child");
    let child = create_session(SessionEngineConfig {
        rlm_depth: Some(1),
        ..fixture.config(&child_provider)
    })
    .await
    .unwrap();
    child
        .prompt("child input", PromptOptions::default())
        .await
        .unwrap();
    child
        .session
        .before_compaction_hook(
            super::compact_session::CompactionTrigger::Auto,
            None,
            &registry_model(),
            None,
        )
        .await
        .unwrap();
    assert!(child.session_end_hook().is_none());
    assert_ne!(
        parent.session.session_id().await,
        child.session.session_id().await
    );
    assert_eq!(std::fs::read(&path).unwrap(), baseline);
    let names = events(&path)
        .into_iter()
        .map(|event| event["hook_event_name"].as_str().unwrap().to_owned())
        .collect::<Vec<_>>();
    assert_eq!(
        names,
        [
            "SessionStart",
            "UserPromptSubmit",
            "Stop",
            "PreCompact",
            "SessionEnd"
        ]
    );
}

#[tokio::test]
async fn hooks_use_host_durable_identity_instead_of_in_memory_mirror_id() {
    let _command_guard = crate::hooks::test_command_guard().await;
    let fixture = Fixture::new(&json!({"hooks": {"SessionEnd": [{"hooks": [{
        "type": "command", "command": "cat > ended.json"
    }]}]}}));
    let provider = Arc::new(ScriptedProvider::new(Model::unknown()));
    let transcript = fixture.cwd.join("durable.jsonl");
    std::fs::write(
        &transcript,
        "{\"type\":\"session\",\"id\":\"host-durable-session\"}\n",
    )
    .unwrap();
    let mut config = fixture.config(&provider);
    config.conversation_log_path = Some(transcript.clone());
    config.semantic_edges = Some(super::semantic_edges::SemanticEdgeIdentity {
        session_id: "host-durable-session".into(),
        ledger_path: None,
        parent_session_id: None,
        spawned_by_request_id: None,
    });
    let engine = create_session(config).await.unwrap();
    engine.session_end_hook().unwrap()().await;
    let observed: Value =
        serde_json::from_slice(&std::fs::read(fixture.cwd.join("ended.json")).unwrap()).unwrap();
    assert_eq!(observed["session_id"], "host-durable-session");
    assert_eq!(
        observed["transcript_path"],
        transcript.display().to_string()
    );
}

#[tokio::test]
async fn late_durable_history_adoption_marks_resume_without_rearming_navigation() {
    let _command_guard = crate::hooks::test_command_guard().await;
    for windowed in [false, true] {
        let fixture = Fixture::new(&json!({"hooks": {"SessionStart": [{"hooks": [{
            "type": "command", "command": "{ cat; printf '\\n'; } >> starts.jsonl"
        }]}]}}));
        let provider = Arc::new(ScriptedProvider::new(Model::unknown()));
        for text in ["first", "second", "third"] {
            provider.push_text_turn(text);
        }
        let engine = create_session(fixture.config(&provider)).await.unwrap();
        let mut durable = crate::session::manager::SessionManager::in_memory(&fixture.cwd);
        durable
            .append_message(SessionAgentMessage::User(pa_types::ai::UserMessage {
                content: pa_types::ai::UserContent::Text("prior user input".into()),
                timestamp: 0,
                rest: serde_json::Map::default(),
            }))
            .unwrap();
        let entries = durable.get_all_entries().to_vec();
        let path = fixture.cwd.join("restored.jsonl");
        let mut bytes = Vec::new();
        for entry in &entries {
            serde_json::to_writer(&mut bytes, entry).unwrap();
            bytes.push(b'\n');
        }
        std::fs::write(&path, bytes).unwrap();
        for turn in 0..3 {
            if turn == 2 {
                engine.session.mark_compaction_hook();
            }
            if windowed {
                let window = crate::session::window::WindowedSessionStore::open(&path)
                    .unwrap()
                    .expect("valid durable history");
                engine.session.restore_windowed_context(window).await;
            } else {
                engine
                    .session
                    .rebuild_branch_context(entries.clone())
                    .await
                    .unwrap();
            }
            engine
                .prompt("continue", PromptOptions::default())
                .await
                .unwrap();
        }
        let starts = events(&fixture.cwd.join("starts.jsonl"));
        assert_eq!(
            starts
                .iter()
                .map(|event| event["source"].clone())
                .collect::<Vec<_>>(),
            vec![json!("resume"), json!("compact")]
        );
        assert_eq!(provider.calls().len(), 3);
    }
}

#[tokio::test]
async fn session_start_stop_is_consumed_before_the_next_submission() {
    let _command_guard = crate::hooks::test_command_guard().await;
    let fixture = Fixture::new(&json!({"hooks": {"SessionStart": [{"hooks": [{
        "type": "command", "command": "{ cat; printf '\\n'; } >> starts.jsonl; printf '%s' '{\"continue\":false,\"stopReason\":\"pause this admission\"}'"
    }]}]}}));
    let provider = Arc::new(ScriptedProvider::new(Model::unknown()));
    provider.push_text_turn("accepted later");
    let engine = create_session(fixture.config(&provider)).await.unwrap();
    let error = engine
        .prompt("blocked", PromptOptions::default())
        .await
        .unwrap_err();
    assert!(error.to_string().contains("pause this admission"));
    assert!(provider.calls().is_empty());
    engine
        .prompt("accepted", PromptOptions::default())
        .await
        .unwrap();
    assert_eq!(events(&fixture.cwd.join("starts.jsonl")).len(), 1);
    assert_eq!(provider.calls().len(), 1);
}

#[tokio::test]
async fn cancelled_session_start_remains_pending_for_next_submission() {
    let _command_guard = crate::hooks::test_command_guard().await;
    let fixture = Fixture::new(&json!({"hooks": {"SessionStart": [{"hooks": [{
        "type": "command", "command": "{ cat; printf '\\n'; } >> starts.jsonl"
    }]}]}}));
    let provider = Arc::new(ScriptedProvider::new(Model::unknown()));
    provider.push_text_turn("accepted after cancellation");
    let engine = create_session(fixture.config(&provider)).await.unwrap();
    let abort = pa_agent::abort::AbortController::new();
    abort.abort();
    assert!(engine
        .session
        .lifecycle_hooks
        .as_ref()
        .unwrap()
        .start("test", abort.signal())
        .await
        .is_err());
    assert!(provider.calls().is_empty());
    engine
        .prompt("accepted", PromptOptions::default())
        .await
        .unwrap();
    let starts = events(&fixture.cwd.join("starts.jsonl"));
    assert_eq!(
        starts
            .iter()
            .map(|event| event["source"].clone())
            .collect::<Vec<_>>(),
        [json!("startup")]
    );
    assert_eq!(provider.calls().len(), 1);
}

#[tokio::test]
async fn custom_only_adoption_keeps_startup_source() {
    let _command_guard = crate::hooks::test_command_guard().await;
    for adoption in ["construction", "branch", "window"] {
        let fixture = Fixture::new(&json!({"hooks": {"SessionStart": [{"hooks": [{
            "type": "command", "command": "{ cat; printf '\\n'; } >> starts.jsonl"
        }]}]}}));
        let provider = Arc::new(ScriptedProvider::new(Model::unknown()));
        provider.push_text_turn("first actual conversation");
        let mut durable = crate::session::manager::SessionManager::in_memory(&fixture.cwd);
        durable
            .append_custom_message(
                "goal_continuation",
                pa_types::ai::UserContent::Text("setup context".into()),
                /*display*/ false,
                None,
            )
            .unwrap();
        let entries = durable.get_all_entries().to_vec();
        let mut config = fixture.config(&provider);
        if adoption == "construction" {
            config.session_manager = Some(durable);
        }
        let engine = create_session(config).await.unwrap();
        if adoption == "branch" {
            engine
                .session
                .rebuild_branch_context(entries)
                .await
                .unwrap();
        } else if adoption == "window" {
            let path = fixture.cwd.join("restored.jsonl");
            let mut bytes = Vec::new();
            for entry in entries {
                serde_json::to_writer(&mut bytes, &entry).unwrap();
                bytes.push(b'\n');
            }
            std::fs::write(&path, bytes).unwrap();
            let window = crate::session::window::WindowedSessionStore::open(&path)
                .unwrap()
                .unwrap();
            engine.session.restore_windowed_context(window).await;
        }
        engine
            .prompt("first", PromptOptions::default())
            .await
            .unwrap();
        assert_eq!(
            events(&fixture.cwd.join("starts.jsonl"))
                .iter()
                .map(|event| event["source"].clone())
                .collect::<Vec<_>>(),
            [json!("startup")],
            "{adoption}"
        );
    }
}
