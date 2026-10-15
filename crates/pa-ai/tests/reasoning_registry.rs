//! Public registry callers must get the same strict replay policy as the facade.
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;

use pa_ai::types::{Context, Model, StopReason, StreamOptions, Usage};
use serde_json::json;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[tokio::test]
async fn public_google_registry_rejects_unsupported_history_without_sending() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let requests = Arc::new(AtomicUsize::new(0));
    let observed = Arc::clone(&requests);
    let server = tokio::spawn(async move {
        loop {
            let (mut socket, _) = listener.accept().await.unwrap();
            observed.fetch_add(1, Ordering::SeqCst);
            let mut request = [0; 8192];
            let _ = socket.read(&mut request).await.unwrap();
            let sse = "data: {\"candidates\":[{\"content\":{\"parts\":[{\"text\":\"UNSUPPORTED_REQUEST_ESCAPED\"}]},\"finishReason\":\"STOP\"}]}\n\n";
            socket.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\n\r\n{sse}", sse.len()).as_bytes()).await.unwrap();
        }
    });
    let model: Model = serde_json::from_value(json!({
        "id": "gemini-destination", "name": "Synthetic Google model", "api": "google-generative-ai", "provider": "google",
        "baseUrl": format!("http://{address}"), "reasoning": true, "input": ["text"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0}, "contextWindow": 128_000, "maxTokens": 8192
    })).unwrap();
    let context = Context {
        system_prompt: None, tools: None,
        messages: vec![serde_json::from_value(json!({
            "role": "assistant", "api": model.api, "provider": model.provider, "model": "gemini-source",
            "timestamp": 0, "stopReason": "stop", "usage": Usage::default(),
            "content": [{"type": "thinking", "thinking": "SYNTHETIC_PRIVATE_PLAN", "thinkingSignature": "U1lOVEhFVElDX1NJR05BVFVSRQ=="}]
        })).unwrap()],
    };
    let original = context.messages.clone();
    pa_ai::registry::ensure_builtins();
    let provider = pa_ai::registry::get_api_provider(&model.api).unwrap();
    let options = StreamOptions {
        api_key: Some("fixture-only".into()),
        ..Default::default()
    };
    let output = tokio::time::timeout(
        Duration::from_secs(5),
        provider.stream(&model, &context, Some(&options)).result(),
    )
    .await
    .unwrap();
    server.abort();
    assert_eq!(context.messages, original);
    assert_eq!(
        requests.load(Ordering::SeqCst),
        0,
        "unsupported replay escaped the public registry boundary"
    );
    assert_eq!(output.stop_reason, StopReason::Error);
    assert!(output
        .error_message
        .unwrap()
        .contains("Cannot replay reasoning"));
}
