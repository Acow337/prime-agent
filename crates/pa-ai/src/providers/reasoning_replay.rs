//! Validate reasoning replay before constructing a destination request. Unsupported or
//! signature-bound history must surface a limitation, never become answer text or disappear.

use crate::providers::openai_completions::{
    decode_reasoning_details, get_compat, REASONING_FIELDS,
};
use crate::types::{AssistantContent, Context, Message, Model, StopReason};

/// Check destination channels and signature bindings without modifying source history.
///
/// # Errors
///
/// Returns a user-visible limitation when the destination cannot retain the reasoning representation.
// Keep destination checks together so a new channel cannot bypass signature validation.
#[allow(clippy::too_many_lines)]
pub(crate) fn validate_reasoning_replay(model: &Model, context: &Context) -> Result<(), String> {
    for message in &context.messages {
        let Message::Assistant(assistant) = message else {
            continue;
        };
        if matches!(
            assistant.stop_reason,
            StopReason::Error | StopReason::Aborted
        ) {
            continue;
        }
        let same_protocol = assistant.provider == model.provider && assistant.api == model.api;
        let same_model = same_protocol && assistant.model == model.id;
        let limitation = |detail: &str| {
            format!(
            "Cannot replay reasoning from {}/{} to {}/{}: {detail}. Original session history is unchanged.",
            assistant.provider, assistant.model, model.provider, model.id,
        )
        };
        for block in &assistant.content {
            if let AssistantContent::ToolCall(call) = block {
                if let Some(signature) = call.thought_signature.as_deref() {
                    if !same_protocol {
                        return Err(limitation("the tool call carries a source-bound thought signature; destination compatibility has not been verified"));
                    }
                    let supported = match model.api.as_str() {
                        "google-generative-ai" | "google-vertex" => {
                            same_model
                                && signature.len() % 4 == 0
                                && !signature.is_empty()
                                && signature.bytes().all(|byte| {
                                    byte.is_ascii_alphanumeric()
                                        || matches!(byte, b'+' | b'/' | b'=')
                                })
                        }
                        "openai-completions" => {
                            serde_json::from_str::<serde_json::Value>(signature)
                                .is_ok_and(|value| value.is_object())
                        }
                        _ => false,
                    };
                    if !supported {
                        return Err(limitation(
                            "the destination adapter cannot replay this tool thought signature",
                        ));
                    }
                }
            }
            let AssistantContent::Thinking(thinking) = block else {
                continue;
            };
            let channel_marker = thinking
                .thinking_signature
                .as_deref()
                .filter(|field| REASONING_FIELDS.contains(field));
            if !same_protocol
                && (thinking.redacted.unwrap_or(false)
                    || thinking.thinking_signature.is_some() && channel_marker.is_none())
            {
                return Err(limitation("the reasoning carries a source-bound signature or opaque payload that cannot be translated into plaintext reasoning"));
            }
            if model.api == "openai-completions" {
                let compat = get_compat(model);
                if compat.requires_thinking_as_text {
                    return Err(limitation(
                        "this route requires reasoning as answer text, which is disabled",
                    ));
                }
                let structured_same_protocol = same_protocol
                    && decode_reasoning_details(thinking.thinking_signature.as_deref()).is_some();
                if thinking.thinking_signature.is_some()
                    && channel_marker.is_none()
                    && !structured_same_protocol
                {
                    return Err(limitation(
                        "the Completions adapter cannot replay this source-bound signature",
                    ));
                }
                if thinking.redacted.unwrap_or(false) && !structured_same_protocol {
                    return Err(limitation(
                        "this route cannot replay the opaque reasoning payload",
                    ));
                }
                if !compat.requires_reasoning_content_on_assistant_messages
                    // A field-name marker is protocol metadata, not a model-bound signature.
                    && !((same_model || same_protocol && model.reasoning) && channel_marker.is_some())
                    && !structured_same_protocol
                {
                    return Err(limitation("the destination has no supported reasoning replay channel for this history"));
                }
            } else if matches!(model.api.as_str(), "google-generative-ai" | "google-vertex")
                && !same_model
            {
                return Err(limitation("the Google adapter cannot retain reasoning and thought signatures across this model switch"));
            } else if model.api == "bedrock-converse-stream" && thinking.redacted.unwrap_or(false) {
                return Err(limitation(
                    "the Bedrock adapter cannot replay this opaque reasoning payload",
                ));
            } else if model.api == "mistral-conversations"
                && thinking
                    .thinking_signature
                    .as_deref()
                    .is_some_and(|signature| !REASONING_FIELDS.contains(&signature))
            {
                return Err(limitation(
                    "the Mistral adapter has no thought-signature replay field",
                ));
            } else if matches!(model.api.as_str(), "google-generative-ai" | "google-vertex")
                && thinking
                    .thinking_signature
                    .as_deref()
                    .is_some_and(|signature| {
                        signature.is_empty()
                            || signature.len() % 4 != 0
                            || !signature.bytes().all(|byte| {
                                byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'/' | b'=')
                            })
                    })
            {
                return Err(limitation(
                    "the original Google thought signature is not valid base64",
                ));
            } else if (model.api == "anthropic-messages"
                || model.api == "bedrock-converse-stream"
                    && model.id.to_ascii_lowercase().contains("claude"))
                && thinking
                    .thinking_signature
                    .as_deref()
                    .is_none_or(|signature| {
                        signature.trim().is_empty() || REASONING_FIELDS.contains(&signature)
                    })
            {
                return Err(limitation(
                    "this destination requires the original reasoning signature",
                ));
            } else if matches!(
                model.api.as_str(),
                "openai-responses" | "azure-openai-responses" | "openai-codex-responses"
            ) && thinking
                .thinking_signature
                .as_deref()
                .and_then(|signature| serde_json::from_str::<serde_json::Value>(signature).ok())
                .is_none_or(|item| {
                    item.get("type").and_then(serde_json::Value::as_str) != Some("reasoning")
                })
            {
                return Err(limitation("the Responses API requires a replayable reasoning item, not unsigned reasoning text"));
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fixture(api: &str, signature: Option<&str>) -> (Model, Context) {
        let model = serde_json::from_value(json!({
            "id": "destination", "name": "fixture", "api": api, "provider": "fixture-provider",
            "baseUrl": "http://localhost", "reasoning": true, "input": ["text"],
            "cost": { "input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0 },
            "contextWindow": 128000, "maxTokens": 8192
        }))
        .unwrap();
        let context = Context {
            system_prompt: None,
            messages: vec![serde_json::from_value(json!({
                "role": "assistant", "api": api, "provider": "fixture-provider", "model": "source",
                "timestamp": 0, "stopReason": "stop", "usage": crate::types::Usage::default(),
                "content": [{ "type": "thinking", "thinking": "SYNTHETIC_PLAN", "thinkingSignature": signature }]
            })).unwrap()],
            tools: None,
        };
        (model, context)
    }

    #[test]
    fn native_reasoning_protocol_does_not_require_identical_model_ids() {
        for (api, signature) in [
            ("anthropic-messages", Some("NATIVE_SIGNATURE")),
            (
                "openai-responses",
                Some(r#"{"type":"reasoning","encrypted_content":"SYNTHETIC_OPAQUE"}"#),
            ),
            ("mistral-conversations", None),
            ("openai-completions", Some("reasoning_content")),
        ] {
            let (model, context) = fixture(api, signature);
            let original = context.messages.clone();
            assert_eq!(validate_reasoning_replay(&model, &context), Ok(()));
            assert_eq!(context.messages, original);
        }
    }

    #[test]
    fn incompatible_representations_fail_without_losing_original_reasoning() {
        for (api, signature, expected) in [
            (
                "anthropic-messages",
                Some("reasoning_content"),
                "requires the original reasoning signature",
            ),
            (
                "openai-responses",
                None,
                "requires a replayable reasoning item",
            ),
            (
                "google-generative-ai",
                Some("YWJj"),
                "cannot retain reasoning",
            ),
            (
                "openai-completions",
                Some("BOUND_SIGNATURE"),
                "cannot replay this source-bound signature",
            ),
        ] {
            let (model, context) = fixture(api, signature);
            let original = context.messages.clone();
            let error = validate_reasoning_replay(&model, &context).unwrap_err();
            assert!(error.contains(expected), "{error}");
            assert!(!error.contains("SYNTHETIC_PLAN"));
            assert_eq!(context.messages, original);
        }
    }
}
