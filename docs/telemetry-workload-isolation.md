# Test and eval telemetry isolation

Production usage counts must describe actual use, including legitimate
headless automation and custom providers/models. A token-count, timing,
platform, version, or execution-mode fingerprint cannot establish that a
run is a test. The emitter behind the October 13–14, 2026 spike is still
unproven; this change neither repairs historical events nor changes live
PostHog data, dashboards, filters, or daily-stat queries.

## Suppression boundaries

- Daemon `AgentSessionEngine::new` makes every configured faux script a
  hard telemetry opt-out before session or model-refusal clients exist.
  A caller's `telemetryDisabled=false` cannot enable a faux run.
- CLI print/RPC faux sessions already omit session telemetry. The
  analytics sink also recognizes `PRIME_AGENT_FAUX_SCRIPT`, covering
  startup and other invocation-level emissions.
- Harnesses using real providers or HTTP mocks declare
  `PRIME_AGENT_TELEMETRY_ORIGIN=test` or `eval`. The analytics sink consumes
  their batches without HTTP requests or retries, including unmarked
  events; per-event `workload_origin=test|eval` is also filtered from mixed
  batches. This sink policy is independent of debug/release mode and
  cannot be overridden by `PRIME_AGENT_TELEMETRY=1`.
- A marked harness may retain a local JSONL mirror for diagnostics.
  `DO_NOT_TRACK=1`, `PI_OFFLINE=1`, and the existing settings switch keep
  their previous precedence and disable recording/delivery, including
  the mirror. No environment variable is rewritten by this policy.
- `rlm.spawn` already forwards the parent's filtered full launch env.
  `rlm.create_session` now preserves telemetry env controls in its minimal
  env. Both carry the parent invocation's hard create-time opt-out through
  `telemetryDisabled`, including across child worker restarts. Faux child
  scripts also encounter the constructor guard in each real engine.

The shared model-refusal telemetry object already carries the parent's
hard opt-out into the child registry; child creation reads that same
immutable policy instead of introducing a second configuration surface.

## Harness audit

Cargo's `.cargo/config.toml` supplies `DO_NOT_TRACK=1` in all profiles,
including release tests and their subprocesses. Cargo does not override
an existing value, so this is a useful default, not a proof that arbitrary
release-binary harnesses cannot emit. Debug builds separately omit the
production endpoint. Targeted instrumentation tests may inject local
telemetry in debug builds only.

The checked-in `review_*_parity.py` launchers set `DO_NOT_TRACK=1` and
`PI_OFFLINE=1`. In particular, `review_goal_backoff_parity.py` has a 10-input,
1-output mock response but explicitly opts out and uses JSON mode; it is
not an established source of the spike. Release compatibility test
launchers also set `DO_NOT_TRACK=1`. Faux integration tests run the real
engine, so they now have a constructor-level guard even if inherited
opt-outs are absent. No runtime inference from CI, model names, tiny
responses, milliseconds, or automated mode is added.

A synthetic session submitted through RPC to an already-running, unmarked
shared supervisor cannot reclassify that whole daemon: its aggregate
`daemon event` operational counters may still include the synthetic
operation. The faux guard suppresses the session's usage/refusal emissions;
it does not filter shared supervisor counters per worker. Dedicated test
and eval daemons must be launched with the marker or opt-out too. Suppressing
an entire shared daemon on its first synthetic session would also discard
legitimate sessions' operational telemetry and is intentionally avoided.

External eval harnesses using real providers must adopt an explicit
`PRIME_AGENT_TELEMETRY_ORIGIN=eval` marker (or `DO_NOT_TRACK=1` to suppress
local diagnostics too). Unmarked real-provider evals remain indistinguishable
from legitimate automation; the fix cannot classify them retroactively.

## Proposed reporting exclusions (not applied)

Keep the existing test-account and `version=0.0.0-benchmark` exclusions.
Add explicit `workload_origin IN ('test', 'eval')` and
`build_channel='development'` exclusions to production adoption reports.
For HogQL, the additional event predicate is:

```sql
coalesce(toString(properties.workload_origin), '') NOT IN ('test', 'eval')
AND coalesce(toString(properties.build_channel), '') != 'development'
```

These markers are producer declarations or build facts, rather than
behavioral guesses. Missing/unknown origin or execution mode stays
included; so do automated usage, custom models, prereleases, and releases.
`internal` is an explicit staff marker but may represent real dogfooding;
report it separately and decide its inclusion as a product metric choice.
Do not infer internal/test/eval status from a user ID's small-run history.
Any historical exclusion using the October fingerprint must be shown as
an estimated diagnostic comparison, separate from a verified production
usage metric.

## Validation

Regression coverage exercises faux opt-in rejection through a real core
engine, parent opt-out on both child depths, minimal child-env preservation,
and actual HTTP sink delivery through loopback stubs. Isolated test
subprocesses prove test/eval/faux env markers suppress even unmarked events
with telemetry explicitly enabled. A mixed batch still delivers a custom,
automated, unknown-mode production event. Hosted CI runs the HTTP sink
suite in release mode as well as the normal workspace checks; compilation
and tests are kept off the local Mac.

Ownership: internal changes in pa-daemon's engine/child supervision and
pa-telemetry's event schema and delivery policy; no new public API,
dependency, cross-crate re-export, or user-visible wire schema change.
