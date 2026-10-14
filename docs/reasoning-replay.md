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

The existing source channel marker is protocol metadata. For a reasoning target
on the same provider/API it can survive a model switch; no model-ID mapping or
new catalog capability is needed. Existing DeepSeek compatibility flags also
select `reasoning_content`. Structured Completions `reasoning_details` remains
native within the same protocol.

There is no blanket rejection for different model IDs. Anthropic and Responses
history uses the original native representation within the same provider/API;
the provider remains responsible for signature bindings and model compatibility.
Preserving a request does not establish that the destination consumes every part.

An adapter limitation is explicit before requesting generation when replay would
otherwise silently lose or mislabel reasoning: foreign opaque signatures, an
unsigned Anthropic block, plaintext without a Responses reasoning item, a
Completions route with no supported channel, a signed Claude block sent to a
Bedrock destination whose serializer has no signature field, or a route requiring thinking as
answer text. Google currently retains thoughts/signatures only for the same
model, including thought signatures attached to answer-text blocks, so a
cross-model Google handoff reports that limitation. Its translation
policy and cross-adapter opaque-signature mappings require separate compatibility
work; this PR does not invent them. Unsupported original history remains stored.

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
Existing stream tests distinguish `delta.content` from `reasoning_content`;
this PR does not change stream parsing or rendering.

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
