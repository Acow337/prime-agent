//! Synthetic payload receipts: serializer interoperability, not cryptographic acceptance.
use super::*;
use crate::types::{CacheRetention, Usage};
use serde_json::json;

fn model(api: &str, provider: &str, id: &str) -> Model {
    serde_json::from_value(json!({
        "id": id, "name": id, "api": api, "provider": provider,
        "baseUrl": "http://localhost", "reasoning": true, "input": ["text"],
        "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
        "contextWindow": 128_000, "maxTokens": 8192
    }))
    .unwrap()
}

fn history(source: &Model, content: &serde_json::Value) -> Context {
    Context {
        system_prompt: Some("Synthetic shared prefix".into()),
        messages: vec![serde_json::from_value(json!({
            "role": "assistant", "api": source.api, "provider": source.provider, "model": source.id,
            "timestamp": 0, "stopReason": "stop", "usage": Usage::default(), "content": content
        }))
        .unwrap()],
        tools: None,
    }
}

#[test]
fn empty_signed_claude_reasoning_round_trips_across_native_adapters() {
    let anthropic = model("anthropic-messages", "anthropic", "claude-sonnet-4-6");
    let bedrock = model(
        "bedrock-converse-stream",
        "amazon-bedrock",
        "anthropic.claude-sonnet-4-6-v1",
    );
    for source in [&anthropic, &bedrock] {
        let context = history(
            source,
            &json!([
                { "type": "thinking", "thinking": "", "thinkingSignature": "SYNTHETIC_SIGNATURE" },
                { "type": "text", "text": "Synthetic answer" }
            ]),
        );
        let original = context.messages.clone();
        assert_eq!(validate_reasoning_replay(&anthropic, &context), Ok(()));
        assert_eq!(validate_reasoning_replay(&bedrock, &context), Ok(()));
        assert_eq!(
            crate::providers::anthropic::convert_messages(
                &context, &anthropic, /*is_oauth_token*/ false, None
            ),
            vec![json!({ "role": "assistant", "content": [
            { "type": "thinking", "thinking": "", "signature": "SYNTHETIC_SIGNATURE" },
            { "type": "text", "text": "Synthetic answer" }
        ] })]
        );
        assert_eq!(
            crate::providers::bedrock::convert_messages(&context, &bedrock, CacheRetention::None),
            vec![json!({ "role": "assistant", "content": [
                { "reasoningContent": { "reasoningText": { "text": "", "signature": "SYNTHETIC_SIGNATURE" } } },
                { "text": "Synthetic answer" }
            ] })]
        );
        assert_eq!(context.messages, original);
    }
}

#[test]
fn opaque_claude_payload_round_trips_without_rewriting_or_becoming_text() {
    let anthropic = model("anthropic-messages", "anthropic", "claude-sonnet-4-6");
    let bedrock = model(
        "bedrock-converse-stream",
        "amazon-bedrock",
        "anthropic.claude-sonnet-4-6-v1",
    );
    let encoded = base64::engine::general_purpose::STANDARD.encode(b"SYNTHETIC_OPAQUE_BYTES");
    for source in [&anthropic, &bedrock] {
        let context = history(
            source,
            &json!([
                { "type": "thinking", "thinking": "[Reasoning redacted]", "thinkingSignature": encoded, "redacted": true }
            ]),
        );
        let original = context.messages.clone();
        assert_eq!(validate_reasoning_replay(&anthropic, &context), Ok(()));
        assert_eq!(validate_reasoning_replay(&bedrock, &context), Ok(()));
        assert_eq!(
            crate::providers::anthropic::convert_messages(
                &context, &anthropic, /*is_oauth_token*/ false, None
            ),
            vec![
                json!({ "role": "assistant", "content": [{ "type": "redacted_thinking", "data": encoded }] })
            ]
        );
        assert_eq!(
            crate::providers::bedrock::convert_messages(&context, &bedrock, CacheRetention::None),
            vec![
                json!({ "role": "assistant", "content": [{ "reasoningContent": { "redactedContent": encoded } }] })
            ]
        );
        assert_eq!(context.messages, original);
    }
}

#[test]
fn plaintext_source_identity_does_not_override_destination_support() {
    let source = model("openai-completions", "zai", "glm-fixture");
    let destination = model(
        "openai-completions",
        "prime-inference",
        "internal/deepseek-fixture",
    );
    let context = history(
        &source,
        &json!([
            { "type": "thinking", "thinking": "SYNTHETIC_PLAN", "thinkingSignature": "reasoning_content" }
        ]),
    );
    let original = context.messages.clone();
    assert_eq!(validate_reasoning_replay(&destination, &context), Ok(()));
    let compat = crate::providers::openai_completions::get_compat(&destination);
    assert_eq!(
        compat.reasoning_replay_field.as_deref(),
        Some("reasoning_content")
    );
    assert_eq!(
        crate::providers::openai_completions::convert_messages(&destination, &context, &compat)
            .unwrap(),
        vec![
            json!({ "role": "system", "content": "Synthetic shared prefix" }),
            json!({ "role": "assistant", "content": "", "reasoning_content": "SYNTHETIC_PLAN" })
        ]
    );
    for field in ["reasoning", "reasoning_text"] {
        let mut configured = model("openai-completions", "configured-destination", "fixture");
        configured.compat =
            Some(serde_json::from_value(json!({ "reasoningReplayField": field })).unwrap());
        let compat = crate::providers::openai_completions::get_compat(&configured);
        assert_eq!(
            crate::providers::openai_completions::convert_messages(&configured, &context, &compat)
                .unwrap(),
            vec![
                json!({ "role": "developer", "content": "Synthetic shared prefix" }),
                json!({ "role": "assistant", "content": "", field: "SYNTHETIC_PLAN" })
            ]
        );
    }
    assert_eq!(context.messages, original);
}

#[test]
fn native_binding_drops_are_explicit_without_signature_or_reasoning_disclosure() {
    for reason in [
        "model_binding_mismatch",
        "organization_binding_mismatch",
        "prefix_binding_mismatch",
    ] {
        let transformations = json!([{ "type": "thinking_dropped", "reason": reason, "path": "messages.1.content.0" }]);
        let error = validate_input_transformations(Some(&transformations)).unwrap_err();
        assert!(error.contains("provider dropped replayed reasoning"));
        assert!(!error.contains("messages.1.content.0"));
    }
    assert_eq!(validate_input_transformations(Some(&json!([]))), Ok(()));
}
