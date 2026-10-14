# Reasoning replay evidence

The legacy cross-model normalizer intentionally converts foreign nonredacted
reasoning into ordinary assistant text to preserve context. The TypeScript
predecessor does the same: commit `46b5800d3` introduced context-preserving
handoffs, and `16e142ef7` removed generated thinking wrappers because models
mimicked the tags. Its v0.9.8 migration test explicitly expects thinking-to-text.

This change intentionally differs from that policy: reasoning stays reasoning.
For a synthetic GLM → DeepSeek switch through the same Prime gateway, the old
request contains `SYNTHETIC_PRIVATE_MARKERSynthetic answer.` in assistant
`content`. The revised request keeps `content: "Synthetic answer."` and sends
`reasoning_content: "SYNTHETIC_PRIVATE_MARKER"` separately. Thinking-only turns
use empty answer content, preserving their assistant boundary and tool pairing.
Fragments concatenate in order without inserting or removing whitespace.
Original persisted messages and signatures are not edited.

## Scope and compatibility

Plaintext reasoning replay selects the destination's supported input field, independent
of source identity. Direct DeepSeek, Z.AI, and reasoning models behind Prime's
Completions adapter accept `reasoning_content`; other Completions destinations can
explicitly declare `compat.reasoningReplayField` as `reasoning_content`, `reasoning`,
or `reasoning_text`. A same-protocol source channel marker remains a compatibility
fallback. Structured Completions `reasoning_details` remains native within the
same protocol. A supported input schema does not prove a deployed model consumes it.

Claude-native Anthropic Messages and Bedrock Converse adapters retain signed thinking,
including empty visible summaries, and map opaque/redacted blocks to their native
representations without changing the payload bytes. Both adapters preserve same-model
history and allow the documented Claude-native bridge across providers. Google Cloud
Claude accessed through Anthropic Messages is part of that native representation;
the Gemini `google-vertex` adapter is a different protocol.

These bridges preserve wire representations, not universal cryptographic readability.
Claude's actual model, linked-account and unchanged-prefix checks remain authoritative.
The request asks for native binding diagnostics and surfaces `thinking_dropped` as an
explicit error, retaining the original history. It never forges signatures, chooses a
prefix relaxation or silently converts unsupported blocks to answer text. An unsupported
beta or signature produces the provider's error rather than an automatic fallback.
See [Claude preserved thinking](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking)
and [thinking encryption](https://platform.claude.com/docs/en/build-with-claude/thinking#thinking-encryption).
No live cross-platform generation or account-link acceptance was tested.

An adapter limitation is explicit before generation when replay would silently lose or
mislabel reasoning: unrelated foreign opaque signatures, unsigned Claude blocks,
plaintext without a Responses reasoning item, a Completions route with no supported
channel, signed Claude reasoning sent to a non-Claude Bedrock model, malformed opaque
payloads, or a route requiring thinking as answer text. Google currently retains
thoughts/signatures only for the same model, including signatures attached to answer
text. Unsupported history remains stored.

## Internal route evidence

Inspected current `inference-iac/routes/prod/internal--deepseek-v4.1-flash.yaml`
(blob `446f44467edb6588fb8c74a72f65d9a39be9202d`), the B300 recipe pinning Dynamo
`8ce4970f9f8497085bb0d2274f0fe56f8ecdc4fc`, and gateway source at platform commit
`1955a2c8f6e2b9d0fbd69fb00a26f9409eeaee46`.
The gateway's ChatMessage schema and body codec preserve `reasoning_content`;
Dynamo protocols 5.4.3 accepts it on assistant messages. The GLM chat template
consumes it. Pinned DeepSeek renderer source consumes it subject to thinking mode
and retention policy: older reasoning drops by default; nonempty tools disable
dropping. Normal Prime Agent requests carry tool definitions. These are source
and configuration observations, not verification of the deployed incident request.
No serving controls were changed. Existing-auth model lookup returned HTTP 403;
no generation request or alternative access was attempted.

## Validation and limits

Regression tests cover channel separation across a model switch, thinking-only
turns, original-history equality, reasoning with matched/missing tool results,
fragment preservation, and native/unsupported signature representations.
Existing stream tests distinguish `delta.content` from `reasoning_content`.
New synthetic regressions cover empty signed Claude blocks and opaque payloads across
both native serializers, byte accumulation at Bedrock stop/EOF, malformed blobs,
destination-selected plaintext channels, and native binding-drop errors including
an Anthropic SSE replay that checks the diagnostic beta. Bedrock stream parsing now
retains opaque bytes; answer rendering is unchanged.

Before the final scope revision, an exact-source lightweight harness passed 28
focused tests. A control replacing only normalization with HEAD failed seven
preservation regressions. Actual TS predecessor execution passed four synthetic
characterization cases. Current gateway serialization and actual GLM Jinja
checks passed offline; DeepSeek golden fixtures were inspected rather than its
Rust formatter being executed. None of these tests used private reasoning traces.
Final native compilation, lint and tests run in hosted CI, not on Kevin's Mac.

## Parity-diff evidence

Wire behavior intentionally diverges from TS v0.9.8 for typed reasoning replay.
The synthetic request comparison above characterizes the deliberate difference;
byte comparison against the full TS daemon and terminal frame/interaction parity
have not been run. No renderer, key/mouse handling or session persistence changed.
Those merge-gate limitations remain explicit; CI cannot prove incident causality.

The visible-thinking report and the nine-minute wait are not established causes
or fixes. The exact incident session and raw request/SSE capture are unavailable.
The waiting timer covers the whole human turn, including tools, so the screenshot
alone cannot identify inference, tool execution or stream termination latency.
