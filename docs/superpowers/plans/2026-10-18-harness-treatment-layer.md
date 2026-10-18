# Harness Treatment Layer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a typed, testable `R/S/M` treatment layer to Prime Agent so experiments can independently reset the REPL after each model turn, disable recursive subagents, and disable all persistent Harness reads and writes without changing normal product behavior.

**Architecture:** `pa-core` owns a new `HarnessTreatment` vocabulary and applies it while assembling a `SessionEngine`. REPL reset is a protocol-level state operation implemented by the Python runtime and exposed through the Rust kernel manager; subagent treatment reuses the no-children host, while Harness treatment pins the existing all-layer `harnessRefinements` gate inside each isolated experiment agent directory. The default `create_session` path never writes treatment settings, so ordinary CLI, daemon wire, session files, and tool names remain unchanged.

**Tech Stack:** Rust 2021, Tokio, `pa-core`, `pa-agent`, Python 3, `unittest`, `uv`, Cargo tests.

---

## Scope decomposition

The approved research design spans four independent subsystems. This plan implements only the first, which must land before the others:

1. **This plan:** typed product-side treatment layer and treatment probes;
2. `2026-10-18-harness-experiment-runner.md`: isolated run manifests, hard budgets, retries, and result schemas;
3. `2026-10-18-harness-benchmark-tasks.md`: I/E/T task generators and verifiers;
4. `2026-10-18-harness-analysis-selector.md`: data audit, mixed-effects analysis, plots, and budget-aware selector.

Each later plan must treat the APIs established here as fixed inputs rather than duplicating treatment logic.

## File map

| File | Responsibility |
| --- | --- |
| `crates/pa-core/src/session_engine/treatment.rs` | Public typed treatment vocabulary and stable treatment ID |
| `crates/pa-core/src/session_engine/mod.rs` | Export treatment module and hold the resolved treatment on `AgentSession` |
| `crates/pa-core/src/session_engine/engine.rs` | Keep `create_session` compatible and apply treatment through `create_session_with_treatment` |
| `crates/pa-core/src/session_engine/rlm_host.rs` | Preserve the existing fail-closed no-children host behavior used by S-off |
| `crates/pa-core/src/session_engine/engine/tests.rs` | Session-level treatment integration tests |
| `crates/pa-core/src/kernel/protocol.rs` | Rust `reset_namespace` request frame |
| `crates/pa-core/src/kernel/manager/mod.rs` | Public manager reset API and result parsing |
| `crates/pa-core/src/kernel/manager/requests.rs` | Mark reset as a namespace mutation for snapshot freshness |
| `crates/pa-core/src/kernel/provisioner.rs` | Provisioner-level reset API used by the session listener |
| `crates/pa-core/tests/kernel_capture_freshness.rs` | Real-runtime reset and snapshot freshness regression |
| `prime-agent-runtime/src/rlm/repl.py` | Execute ordered namespace reset while preserving runtime bindings |
| `prime-agent-runtime/test/test_repl.py` | REPL reset protocol regression |

### Task 1: Add the typed treatment vocabulary

**Files:**
- Create: `crates/pa-core/src/session_engine/treatment.rs`
- Modify: `crates/pa-core/src/session_engine/mod.rs`
- Modify: `crates/pa-core/src/session_engine/engine.rs`

- [ ] **Step 1: Write the failing vocabulary tests**

Create `crates/pa-core/src/session_engine/treatment.rs` with the tests first:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_is_the_unchanged_full_product() {
        assert_eq!(HarnessTreatment::default(), HarnessTreatment::FULL);
        assert_eq!(HarnessTreatment::FULL.id(), "r1-s1-m1");
    }

    #[test]
    fn stable_id_encodes_each_independent_factor() {
        let treatment = HarnessTreatment {
            repl: ReplPersistence::ResetAfterTurn,
            subagents: SubagentAccess::Disabled,
            harness_state: HarnessStateAccess::Disabled,
        };
        assert_eq!(treatment.id(), "r0-s0-m0");
    }
}
```

Add `pub mod treatment;` beside the other public `session_engine` modules in `mod.rs`. Do not define the production types yet.

- [ ] **Step 2: Run the focused test and verify red**

Run:

```bash
cargo test -p pa-core session_engine::treatment::tests --lib
```

Expected: compilation fails because `HarnessTreatment`, `ReplPersistence`, `SubagentAccess`, and `HarnessStateAccess` are undefined.

- [ ] **Step 3: Add the minimal treatment types**

Place this production code above the tests in `treatment.rs`:

```rust
/// Whether Python values survive from one settled model turn to the next.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ReplPersistence {
    #[default]
    AcrossTurns,
    ResetAfterTurn,
}

/// Whether this session may create recursive child sessions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SubagentAccess {
    #[default]
    Enabled,
    Disabled,
}

/// Whether persistent Harness state may be read, injected, or changed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum HarnessStateAccess {
    #[default]
    Enabled,
    Disabled,
}

/// Orthogonal Harness factors used by controlled evaluation runs.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct HarnessTreatment {
    pub repl: ReplPersistence,
    pub subagents: SubagentAccess,
    pub harness_state: HarnessStateAccess,
}

impl HarnessTreatment {
    pub const FULL: Self = Self {
        repl: ReplPersistence::AcrossTurns,
        subagents: SubagentAccess::Enabled,
        harness_state: HarnessStateAccess::Enabled,
    };

    /// Stable factor ID written into experiment manifests.
    #[must_use]
    pub const fn id(self) -> &'static str {
        match (self.repl, self.subagents, self.harness_state) {
            (ReplPersistence::AcrossTurns, SubagentAccess::Enabled, HarnessStateAccess::Enabled) => "r1-s1-m1",
            (ReplPersistence::AcrossTurns, SubagentAccess::Enabled, HarnessStateAccess::Disabled) => "r1-s1-m0",
            (ReplPersistence::AcrossTurns, SubagentAccess::Disabled, HarnessStateAccess::Enabled) => "r1-s0-m1",
            (ReplPersistence::AcrossTurns, SubagentAccess::Disabled, HarnessStateAccess::Disabled) => "r1-s0-m0",
            (ReplPersistence::ResetAfterTurn, SubagentAccess::Enabled, HarnessStateAccess::Enabled) => "r0-s1-m1",
            (ReplPersistence::ResetAfterTurn, SubagentAccess::Enabled, HarnessStateAccess::Disabled) => "r0-s1-m0",
            (ReplPersistence::ResetAfterTurn, SubagentAccess::Disabled, HarnessStateAccess::Enabled) => "r0-s0-m1",
            (ReplPersistence::ResetAfterTurn, SubagentAccess::Disabled, HarnessStateAccess::Disabled) => "r0-s0-m0",
        }
    }
}
```

Add the resolved treatment to `SessionEngine` so the experiment runner can record it without re-reading configuration:

```rust
pub harness_treatment: super::treatment::HarnessTreatment,
```

Keep every existing caller source-compatible by making the current public function a wrapper:

```rust
pub async fn create_session(config: SessionEngineConfig) -> anyhow::Result<SessionEngine> {
    create_session_inner(config, super::treatment::HarnessTreatment::FULL).await
}

pub async fn create_session_with_treatment(
    config: SessionEngineConfig,
    harness_treatment: super::treatment::HarnessTreatment,
) -> anyhow::Result<SessionEngine> {
    create_session_inner(config, harness_treatment).await
}
```

Rename the existing `create_session` body to `create_session_inner` and give it the additional
`harness_treatment` parameter. Task 6 adds the isolated settings pin after its red test exists.

Initialize `SessionEngine::harness_treatment` from the `harness_treatment` argument. Do not add a field to
`SessionEngineConfig`: the normal product entry point must remain source-compatible and always select `FULL`.

- [ ] **Step 4: Run the vocabulary and engine compile tests**

Run:

```bash
cargo test -p pa-core session_engine::treatment::tests --lib
cargo check -p pa-core --tests
```

Expected: both commands exit 0; every existing `create_session` call retains full-product behavior without call-site edits.

- [ ] **Step 5: Commit the vocabulary**

```bash
git add crates/pa-core/src/session_engine/treatment.rs crates/pa-core/src/session_engine/mod.rs crates/pa-core/src/session_engine/engine.rs
git commit -m "feat(core): add harness treatment vocabulary"
```

### Task 2: Add the runtime namespace-reset protocol

**Files:**
- Modify: `prime-agent-runtime/test/test_repl.py`
- Modify: `prime-agent-runtime/src/rlm/repl.py`
- Modify: `crates/pa-core/src/kernel/protocol.rs`

- [ ] **Step 1: Add a failing Python protocol regression**

Add this method near `test_list_names` in `prime-agent-runtime/test/test_repl.py`:

```python
def test_reset_namespace_clears_user_values_and_preserves_runtime_bindings(self):
    created = self.repl.execute(
        "reset-1",
        "import rlm\nimport math\npublic_value = 7\n_private_value = 9",
    )
    self.assertEqual(one(created, "done")["status"], "ok")

    self.repl.send({"type": "reset_namespace", "id": "reset-2"})
    done = one(self.repl.until_done("reset-2"), "done")
    self.assertEqual(done["status"], "ok")
    self.assertEqual(done["cleared"], ["_private_value", "math", "public_value"])

    probe = self.repl.execute(
        "reset-3",
        "('public_value' in globals(), '_private_value' in globals(), 'rlm' in globals())",
    )
    self.assertEqual(one(probe, "result")["text"], "(False, False, True)")
```

- [ ] **Step 2: Run the Python test and verify red**

Run:

```bash
cd prime-agent-runtime
uv run python -m unittest test.test_repl.ReplTest.test_reset_namespace_clears_user_values_and_preserves_runtime_bindings
```

Expected: FAIL with protocol error `unknown request type: 'reset_namespace'`.

- [ ] **Step 3: Implement ordered reset in the Python runtime**

In `repl.py`, add:

```python
def _reset_namespace(ns: dict[str, Any]) -> list[str]:
    """Drop user bindings while preserving the runtime bridge and builtins."""
    preserved = _ALWAYS_SKIP | {"__builtins__"}
    cleared = sorted(
        name
        for name in ns
        if isinstance(name, str) and not name.startswith("__") and name not in preserved
    )
    for name in cleared:
        ns.pop(name, None)
    return cleared


async def _handle_reset_namespace(req: dict[str, Any], ns: dict[str, Any]) -> None:
    cleared = _reset_namespace(ns)
    _send({"event": "done", "id": req["id"], "status": "ok", "cleared": cleared})
```

Register the new request in all three protocol tables/routes:

```python
_REQUIRED_FIELDS = {
    # existing entries...
    "reset_namespace": ("id",),
}
```

```python
elif rtype == "reset_namespace":
    await _handle_request(_handle_reset_namespace, req, ns)
```

Do not add reset to `_inflight`: it is a short FIFO state operation with no interrupt phase. The
Rust manager serializes it on the execution queue, and the Python `_serve` loop serializes direct
protocol clients behind the currently running request.

- [ ] **Step 4: Add the Rust request shape and its failing assertion**

In `kernel/protocol.rs`, first extend `request_json_round_trip`:

```rust
assert_eq!(Request::ResetNamespace.to_json(), json!({ "type": "reset_namespace" }));
assert_eq!(Request::ResetNamespace.type_name(), "reset_namespace");
```

Then add `ResetNamespace` to `Request`, `type_name`, and `to_json`:

```rust
ResetNamespace,
```

```rust
Request::ResetNamespace => "reset_namespace",
```

```rust
Request::ResetNamespace => json!({ "type": "reset_namespace" }),
```

- [ ] **Step 5: Run protocol tests**

Run:

```bash
cd prime-agent-runtime
uv run python -m unittest test.test_repl.ReplTest.test_reset_namespace_clears_user_values_and_preserves_runtime_bindings
cd ..
cargo test -p pa-core kernel::protocol::tests::request_json_round_trip --lib
```

Expected: both tests PASS.

- [ ] **Step 6: Commit the protocol**

```bash
git add prime-agent-runtime/src/rlm/repl.py prime-agent-runtime/test/test_repl.py crates/pa-core/src/kernel/protocol.rs
git commit -m "feat(kernel): add namespace reset protocol"
```

### Task 3: Expose reset through the manager and provisioner

**Files:**
- Modify: `crates/pa-core/src/kernel/manager/requests.rs`
- Modify: `crates/pa-core/src/kernel/manager/mod.rs`
- Modify: `crates/pa-core/src/kernel/provisioner.rs`
- Modify: `crates/pa-core/tests/kernel_capture_freshness.rs`

- [ ] **Step 1: Write the failing real-runtime reset test**

Add to `kernel_capture_freshness.rs`, using its existing `test_options`, `execute`,
`manifest_path_in`, and `file_bytes` helpers:

```rust
#[tokio::test]
async fn namespace_reset_clears_values_and_invalidates_snapshot_freshness() {
    let dir = tempfile::TempDir::new().expect("temp dir");
    let manifest_path = manifest_path_in(dir.path());
    let Some(options) = test_options(Some(dir.path())) else {
        return;
    };
    let manager = ReplKernelManager::new(options);
    manager
        .start(KernelStartOptions::default())
        .await
        .expect("kernel must start");

    let defined = execute(&manager, "value_kept_only_before_reset = 41").await;
    assert_eq!(defined.status, ExecuteStatus::Ok);
    let committed = manager.snapshot_state().await.expect("initial snapshot");
    assert!(committed
        .saved
        .iter()
        .any(|name| name == "value_kept_only_before_reset"));
    let committed_manifest = file_bytes(&manifest_path);

    let cleared = manager.reset_namespace().await.expect("reset namespace");
    assert!(cleared.contains(&"value_kept_only_before_reset".to_string()));
    let names = manager.list_namespace_names(None).await.expect("names");
    assert!(!names.contains(&"value_kept_only_before_reset".to_string()));

    let post_reset = manager.snapshot_state().await.expect("post-reset snapshot");
    assert!(!post_reset
        .saved
        .iter()
        .any(|name| name == "value_kept_only_before_reset"));
    assert_ne!(
        file_bytes(&manifest_path),
        committed_manifest,
        "reset must invalidate the fresh-capture memo"
    );
    manager.kill();
}
```

- [ ] **Step 2: Run the test and verify red**

Run:

```bash
cargo test -p pa-core --test kernel_capture_freshness namespace_reset_clears_values_and_invalidates_snapshot_freshness -- --nocapture
```

Expected: compilation fails because `reset_namespace` does not exist.

- [ ] **Step 3: Mark reset as a namespace mutation**

In `kernel/manager/requests.rs`, change the request classification to:

```rust
let (namespace_code, restores_namespace) = match &request {
    Request::Execute { .. } | Request::ResetNamespace => (true, false),
    Request::Restore { .. } => (false, true),
    Request::Interrupt
    | Request::HostReply { .. }
    | Request::Snapshot { .. }
    | Request::ListNames
    | Request::McpStatus { .. }
    | Request::Shutdown => (false, false),
};
```

This deliberately uses an exhaustive match so a future state-mutating request cannot silently bypass snapshot freshness.

- [ ] **Step 4: Add the manager API**

Add beside `list_namespace_names` in `kernel/manager/mod.rs`:

```rust
/// Clear user-defined namespace values while preserving runtime bindings.
///
/// # Errors
///
/// Returns an error when the kernel is not running or the reset request fails.
pub async fn reset_namespace(&self) -> anyhow::Result<Vec<String>> {
    if !self.is_running() {
        anyhow::bail!("Kernel is not running");
    }
    let result = self
        .enqueue_request(
            Request::ResetNamespace,
            ExecuteOptions {
                internal: true,
                ..ExecuteOptions::default()
            },
            None,
        )
        .await?;
    if result.result.status != ExecuteStatus::Ok {
        anyhow::bail!("namespace reset failed: {}", describe_failure(&result.result));
    }
    Ok(result
        .done_fields
        .as_ref()
        .and_then(|fields| fields.get("cleared"))
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(str::to_string)
        .collect())
}
```

- [ ] **Step 5: Add the provisioner API**

Add beside `list_namespace_names` in `kernel/provisioner.rs`:

```rust
/// Ensure the kernel is live, then clear its user namespace.
///
/// # Errors
///
/// Returns a startup or reset protocol error.
pub async fn reset_namespace(&self) -> anyhow::Result<Vec<String>> {
    self.ensure(None, None).await?.reset_namespace().await
}
```

- [ ] **Step 6: Run the kernel tests**

Run:

```bash
cargo test -p pa-core --test kernel_capture_freshness namespace_reset_clears_values_and_invalidates_snapshot_freshness -- --nocapture
cargo test -p pa-core kernel::protocol --lib
```

Expected: both commands exit 0. The reset test must show the pre-reset name absent after reset and must complete a new snapshot.

- [ ] **Step 7: Commit the Rust reset API**

```bash
git add crates/pa-core/src/kernel/manager/requests.rs crates/pa-core/src/kernel/manager/mod.rs crates/pa-core/src/kernel/provisioner.rs crates/pa-core/tests/kernel_capture_freshness.rs
git commit -m "feat(core): expose kernel namespace reset"
```

### Task 4: Reset the REPL at every model-turn boundary under R-off

**Files:**
- Modify: `crates/pa-core/src/session_engine/engine/tests.rs`
- Modify: `crates/pa-core/src/session_engine/engine.rs`

- [ ] **Step 1: Write a failing two-turn treatment test**

Add these shared fixtures to `engine/tests.rs`; Tasks 5–7 use the same functions:

```rust
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

fn tool_text(
    messages: &[pa_agent::types::AgentMessage],
    tool_call_id: &str,
) -> String {
    messages
        .iter()
        .find_map(|message| match message {
            pa_agent::types::AgentMessage::Standard(
                pa_agent::types::Message::ToolResult(result),
            ) if result.tool_call_id == tool_call_id => Some(
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
```

Then add a helper that performs two real `python_repl` tool turns and returns only the second tool result:

```rust
async fn run_two_python_turns(
    repl: super::super::treatment::ReplPersistence,
) -> String {
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
```

Finally add the assertion. The first assistant turn calls `python_repl` with
`cross_turn_value = 17`; the second inspects that name:

```rust
#[tokio::test]
async fn repl_treatment_resets_only_between_model_turns() {
    let persistent = run_two_python_turns(ReplPersistence::AcrossTurns).await;
    let stateless = run_two_python_turns(ReplPersistence::ResetAfterTurn).await;
    assert!(persistent.contains("17"));
    assert!(stateless.contains("missing"));
}
```

- [ ] **Step 2: Run the test and verify red**

Run:

```bash
cargo test -p pa-core session_engine::engine::tests::repl_treatment_resets_only_between_model_turns --lib -- --nocapture
```

Expected: FAIL because both treatments retain `cross_turn_value`.

- [ ] **Step 3: Install the TurnEnd listener**

In `create_session`, after the `Arc<Agent>` and provisioner exist, install this listener only for R-off:

```rust
if harness_treatment.repl
    == super::treatment::ReplPersistence::ResetAfterTurn
{
    let provisioner_at_turn_end = std::sync::Arc::downgrade(&provisioner);
    agent
        .subscribe(move |event, _signal| {
            let provisioner_at_turn_end = provisioner_at_turn_end.clone();
            Box::pin(async move {
                if matches!(event, pa_agent::types::AgentEvent::TurnEnd { .. }) {
                    if let Some(provisioner) = provisioner_at_turn_end.upgrade() {
                        provisioner.reset_namespace().await?;
                    }
                }
                Ok(())
            })
        })
        .await;
}
```

Use a weak provisioner reference because the listener lives on the Agent owned by the engine. Do not swallow reset errors: a treatment run with a failed reset is invalid and must fail loudly.

- [ ] **Step 4: Run the focused test and adjacent kernel lifecycle tests**

Run:

```bash
cargo test -p pa-core session_engine::engine::tests::repl_treatment_resets_only_between_model_turns --lib -- --nocapture
cargo test -p pa-core --test kernel_lifecycle
```

Expected: both commands exit 0.

- [ ] **Step 5: Commit turn-boundary reset**

```bash
git add crates/pa-core/src/session_engine/engine.rs crates/pa-core/src/session_engine/engine/tests.rs
git commit -m "feat(core): reset treated repl after each turn"
```

### Task 5: Apply S-off at host assembly and prompt construction

**Files:**
- Modify: `crates/pa-core/src/session_engine/engine/tests.rs`
- Modify: `crates/pa-core/src/session_engine/engine.rs`

- [ ] **Step 1: Write the failing S-off integration test**

Add this minimal counting host to `engine/tests.rs`:

```rust
#[derive(Default)]
struct CountingHost {
    spawns: std::sync::atomic::AtomicUsize,
}

impl super::super::rlm_host::RlmSubagentHost for CountingHost {
    fn spawn(
        &self,
        _request: super::super::rlm_host::RlmSpawnRequest,
    ) -> super::super::rlm_host::RlmHostFuture<super::super::rlm_host::RlmSpawnHandle> {
        self.spawns.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
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
    ) -> super::super::rlm_host::RlmHostFuture<super::super::rlm_host::RlmDeleteSubagentResult> {
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
```

Then add the engine test. It executes a real kernel call so the rejection is tested at the
model-visible surface, not only by prompt text:

```rust
#[tokio::test]
async fn subagent_treatment_removes_spawn_surface_and_never_calls_host() {
    let host = std::sync::Arc::new(CountingHost::default());
    let provider = std::sync::Arc::new(ScriptedProvider::new(treatment_model()));
    let (_tmp, engine) = treatment_engine(
        HarnessTreatment {
            subagents: SubagentAccess::Disabled,
            ..HarnessTreatment::FULL
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
        .await;
    let result = result.unwrap();
    assert_eq!(result.status, crate::kernel::shared::ExecuteStatus::Error);
    assert!(result
        .error
        .as_ref()
        .is_some_and(|error| error.evalue.contains("no RLM child runtime")));
    assert_eq!(host.spawns.load(std::sync::atomic::Ordering::SeqCst), 0);
}
```

- [ ] **Step 2: Run the test and verify red**

Run:

```bash
cargo test -p pa-core session_engine::engine::tests::subagent_treatment_removes_spawn_surface_and_never_calls_host --lib -- --nocapture
```

Expected: FAIL because the configured counting host remains wired and receives the spawn.

- [ ] **Step 3: Resolve the subagent host once**

Near the start of `create_session`, before `wire_session_runtime`, add:

```rust
let subagent_host = match harness_treatment.subagents {
    super::treatment::SubagentAccess::Enabled => config.rlm_subagent_host.clone(),
    super::treatment::SubagentAccess::Disabled => None,
};
```

Pass `subagent_host.clone()` into `RlmWiring`. In `BuildSystemPromptOptions`, set:

```rust
allow_recursion: match harness_treatment.subagents {
    super::treatment::SubagentAccess::Enabled => config.allow_recursion,
    super::treatment::SubagentAccess::Disabled => Some(false),
},
can_spawn: subagent_host.is_some(),
```

Do not add a second spawn rejection in `pa-daemon`: the engine already replaces the host with `NoRlmChildren`, which is the single fail-closed boundary used by embedded sessions.

- [ ] **Step 4: Run focused RLM and prompt tests**

Run:

```bash
cargo test -p pa-core session_engine::engine::tests::subagent_treatment_removes_spawn_surface_and_never_calls_host --lib -- --nocapture
cargo test -p pa-core session_engine::rlm_host --lib
cargo test -p pa-core --test prompt_guards
```

Expected: all commands exit 0; the recording host sees zero spawn calls.

- [ ] **Step 5: Commit S-off**

```bash
git add crates/pa-core/src/session_engine/engine.rs crates/pa-core/src/session_engine/engine/tests.rs
git commit -m "feat(core): gate subagents by harness treatment"
```

### Task 6: Apply M-off to host context and direct Python writes

**Files:**
- Modify: `crates/pa-core/src/session_engine/engine.rs`
- Modify: `crates/pa-core/src/session_engine/engine/tests.rs`

- [ ] **Step 1: Add failing isolated-settings treatment tests**

Add to `engine/tests.rs`:

```rust
#[tokio::test]
async fn experiment_harness_treatment_pins_the_isolated_agent_dir() {
    let provider = std::sync::Arc::new(ScriptedProvider::new(treatment_model()));
    let (tmp, engine) = treatment_engine(
        HarnessTreatment {
            harness_state: HarnessStateAccess::Disabled,
            ..HarnessTreatment::FULL
        },
        &provider,
        None,
    )
    .await;

    let settings = crate::settings::SettingsManager::create(
        &tmp.path().join("project"),
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
    assert!(result.error.as_ref().is_some_and(|error| {
        error.evalue.contains("disabled in /settings")
    }));
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
    assert!(!crate::settings::SettingsManager::create(&cwd, &agent_dir)
        .get_harness_refinements());
}
```

- [ ] **Step 2: Run both tests and verify red**

Run:

```bash
cargo test -p pa-core session_engine::engine::tests::experiment_harness_treatment_pins_the_isolated_agent_dir --lib -- --nocapture
cargo test -p pa-core session_engine::engine::tests::normal_create_session_preserves_the_users_harness_setting --lib -- --nocapture
```

Expected: the experiment test fails because M-off has not pinned the setting. The normal-session
regression already passes and must remain green.

- [ ] **Step 3: Pin the existing gate only in the experimental entry point**

Add a private policy beside the session entry points:

```rust
#[derive(Debug, Clone, Copy)]
enum TreatmentSettingsPolicy {
    Preserve,
    Pin(super::treatment::HarnessStateAccess),
}
```

Change only the wrapper calls:

```rust
pub async fn create_session(config: SessionEngineConfig) -> anyhow::Result<SessionEngine> {
    create_session_inner(
        config,
        super::treatment::HarnessTreatment::FULL,
        TreatmentSettingsPolicy::Preserve,
    )
    .await
}

pub async fn create_session_with_treatment(
    config: SessionEngineConfig,
    harness_treatment: super::treatment::HarnessTreatment,
) -> anyhow::Result<SessionEngine> {
    create_session_inner(
        config,
        harness_treatment,
        TreatmentSettingsPolicy::Pin(harness_treatment.harness_state),
    )
    .await
}
```

Add `treatment_settings_policy: TreatmentSettingsPolicy` to the private `create_session_inner`
signature. Immediately after its existing settings load, apply the pin:

```rust
let (mut settings, trace_consent) =
    crate::agent_traces::ContinuousTraceUpload::load_settings(&cwd, &config.agent_dir);
if let TreatmentSettingsPolicy::Pin(access) = treatment_settings_policy {
    settings.set_harness_refinements(
        access == super::treatment::HarnessStateAccess::Enabled,
    )?;
}
```

All existing Rust and Python Harness paths already read the resulting isolated `settings.json`.
Do not add another gate, delete Harness files, or mutate settings in the normal `create_session`
wrapper.

- [ ] **Step 4: Run M-off and existing live-gate regressions**

Run:

```bash
cargo test -p pa-core session_engine::engine::tests::experiment_harness_treatment_pins_the_isolated_agent_dir --lib -- --nocapture
cargo test -p pa-core session_engine::engine::tests::normal_create_session_preserves_the_users_harness_setting --lib -- --nocapture
cargo test -p pa-core session_engine::refinement_gate --lib
cargo test -p pa-core session_engine::harness_digest --lib
uv --directory prime-agent-runtime run python -m unittest test.test_harness
```

Expected: every command exits 0. The experiment path rejects direct Python Harness writes with the
existing `/settings` error; the normal path preserves the user's saved setting.

- [ ] **Step 5: Commit M-off**

```bash
git add crates/pa-core/src/session_engine/engine.rs crates/pa-core/src/session_engine/engine/tests.rs
git commit -m "feat(core): gate harness state by experiment treatment"
```

### Task 7: Verify all eight treatments and the unchanged default

**Files:**
- Modify: `crates/pa-core/src/session_engine/engine/tests.rs`
- Modify: `crates/pa-core/README.md`

- [ ] **Step 1: Add the matrix smoke test**

Add a table-driven engine test that constructs all eight treatments and asserts the resolved
observable facts. It reuses the exact `treatment_model`, `treatment_engine`, and `CountingHost`
fixtures defined in Tasks 4–5:

```rust
#[tokio::test]
async fn every_harness_treatment_matches_its_factor_id_and_surfaces() {
    for repl in [ReplPersistence::AcrossTurns, ReplPersistence::ResetAfterTurn] {
        for subagents in [SubagentAccess::Enabled, SubagentAccess::Disabled] {
            for harness_state in [HarnessStateAccess::Enabled, HarnessStateAccess::Disabled] {
                let treatment = HarnessTreatment { repl, subagents, harness_state };
                let provider = std::sync::Arc::new(ScriptedProvider::new(treatment_model()));
                let host = std::sync::Arc::new(CountingHost::default());
                let (_tmp, engine) = treatment_engine(
                    treatment,
                    &provider,
                    Some(host),
                )
                .await;
                assert_eq!(engine.harness_treatment, treatment, "{}", treatment.id());
                assert_eq!(
                    engine
                        .system_prompt
                        .contains("Subagent spawning is disabled in this session."),
                    subagents == SubagentAccess::Disabled,
                    "{}",
                    treatment.id(),
                );
                assert_eq!(
                    engine.session.harness_refinements_enabled(),
                    harness_state == HarnessStateAccess::Enabled,
                    "{}",
                    treatment.id(),
                );
            }
        }
    }
}
```

- [ ] **Step 2: Run the matrix test**

Run:

```bash
cargo test -p pa-core session_engine::engine::tests::every_harness_treatment_matches_its_factor_id_and_surfaces --lib -- --nocapture
```

Expected: PASS for all eight rows. A failure names the stable treatment ID whose resolved surface
does not match its factors; fix that treatment boundary before continuing.

- [ ] **Step 3: Document the architectural surface**

Add to `crates/pa-core/README.md` under the session-engine public API:

```markdown
Controlled evaluation treatments live in `session_engine::treatment`. They are
off the public CLI and default to the full product. Experiment hosts may disable
cross-turn REPL persistence, recursive child admission, or persistent Harness
state independently; normal sessions never infer a treatment from ambient state.
```

State explicitly in the eventual PR description that this is a new narrow `pub` surface, introduces no dependency, preserves crate ownership, and leaves all user-visible defaults unchanged.

- [ ] **Step 4: Run the complete treatment verification**

Run:

```bash
uv --directory prime-agent-runtime run python -m unittest test.test_repl test.test_harness
cargo fmt --all --check
cargo clippy -p pa-core --all-targets -- -D warnings
cargo test -p pa-core
```

Expected: Python tests report `OK`; fmt, clippy, and all `pa-core` tests exit 0 with no warnings.

- [ ] **Step 5: Run workspace gates before integration**

Run:

```bash
make check
```

Expected: workspace fmt, clippy, tests, and release build all exit 0. If unrelated pre-existing failures occur, record their exact commands and output rather than weakening or skipping the treatment tests.

- [ ] **Step 6: Commit the matrix and documentation**

```bash
git add crates/pa-core/src/session_engine/engine/tests.rs crates/pa-core/README.md
git commit -m "test(core): verify harness treatment matrix"
```

## Completion evidence

The treatment-layer milestone is complete only when all of the following evidence exists:

- `HarnessTreatment::FULL` is the default at every existing `SessionEngineConfig` call site;
- the Python reset test proves user names disappear and runtime bindings survive;
- the real-kernel test proves reset invalidates snapshot freshness;
- the two-turn engine test proves reset occurs after each `TurnEnd`, not within a tool call;
- the recording host proves S-off admits no child;
- the Python and engine tests prove M-off prevents both context injection and direct writes;
- the eight-row matrix resolves stable IDs and expected prompt/digest surfaces;
- `make check` passes without changing dependencies or public CLI/wire behavior.
