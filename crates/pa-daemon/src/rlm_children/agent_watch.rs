//! `rlm.watch.agent`: supervisor-owned activity subscriptions over the
//! parent session's child ledger (the nano-rlm watch series, stage 1).
//!
//! Ownership: one registry per parent session worker, held beside the
//! child ledger in [`super::SupervisorChildSessionsInner`]. The registry
//! and its shared poller live in the worker process, so a Python kernel
//! restart (or a cell ending) never drops a subscription; a parent
//! session worker restart rebuilds the registry empty (restart recovery
//! is explicitly out of the nano-rlm scope). Owner teardown
//! ([`Self::shutdown_agent_watch`], called from the close walk) cancels
//! every subscription and aborts the poller.
//!
//! Events are half-open message-index ranges plus activity transitions —
//! never child transcript content (enforced by construction: the child
//! snapshot is a count and a status label). Two sinks see one event: the
//! bounded readable ring (`rlm.watch.read`) and one coalesced
//! `display: false` wake row per poll batch
//! ([`AGENT_WATCH_NOTICE_CUSTOM_TYPE`], routed like the terminal
//! notices).

use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use std::time::Duration;

use anyhow::{anyhow, bail, Result};

use pa_core::session_engine::rlm_host::{
    RlmAgentWatch, RlmWatchAgentContent, RlmWatchEvent, RlmWatchReadPage, RlmWatchStatusChange,
};
use pa_core::session_engine::rlm_notices::{create_agent_watch_notice, AgentWatchNoticeLine};

use super::SupervisorChildSessionsInner;
use crate::util::now_ms;

/// Live subscriptions per owner (TS parity: `AGENT_WATCH_MAX_ACTIVE`).
pub(crate) const AGENT_WATCH_MAX_ACTIVE: usize = 64;
/// Monotonic registrations per owner; cancels never refund (TS
/// `totalRegistered`).
pub(crate) const AGENT_WATCH_MAX_TOTAL: u64 = 1024;
/// The shared poll cadence (TS `AGENT_WATCH_POLL_INTERVAL_MS`).
pub(crate) const AGENT_WATCH_POLL_INTERVAL_MS: u64 = 5_000;
/// One child snapshot's await bound inside a poll tick: an
/// unresponsive child costs one bounded slice per tick, never the
/// 30s `STATE_TIMEOUT_MS` of head-of-line blocking, so a healthy
/// sibling's event is not withheld behind dead links.
const AGENT_WATCH_SNAPSHOT_TIMEOUT_MS: u64 = 5_000;
/// The snapshot fan-out bound per tick: a chunk's link round trips
/// proceed concurrently (the batch is still one applied lock hold and
/// one wake row), and the load stays bounded at 64 subscriptions.
const AGENT_WATCH_SNAPSHOT_CONCURRENCY: usize = 8;
/// Failed child polls before a subscription gives up with one terminal
/// `-> unreachable` event (the settle watcher's
/// `WATCH_MAX_UNREACHABLE_POLLS` give-up shape, subscription-local and
/// far shorter: a watch is advisory, never a liveness gate).
const WATCH_SUB_MAX_UNREACHABLE_POLLS: u32 = 12;
/// The readable event ring: bounded by entries and serialized bytes,
/// drop-oldest, and every drop is counted at the read surface.
const AGENT_WATCH_EVENT_RING_MAX: usize = 1024;
const AGENT_AGENT_WATCH_EVENT_RING_MAX_BYTES: usize = 256 * 1024;
/// Server-side page cap for one `rlm.watch.read` (the pa-core handler
/// pins the same bound before the host sees the request).
const AGENT_WATCH_READ_LIMIT_MAX: u64 = 500;

/// One subscription's lifecycle (the registry row's `status`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum WatchStatus {
    /// Elegible for the poller.
    Active,
    /// Cancelled by the owner (an idempotent cancel, teardown).
    Cancelled,
    /// Terminal without a cancel: the child vanished or stayed
    /// unreachable past the give-up bound. Inspectable, never re-fires.
    Stopped,
}

impl WatchStatus {
    fn as_str(self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Cancelled => "cancelled",
            Self::Stopped => "stopped",
        }
    }
}

/// One agent activity subscription. The deterministic id
/// (`watch-agent-<rlm_child_id>`) makes a re-register a re-baseline, not
/// a second subscription (TS cancel-then-register).
#[derive(Debug)]
struct AgentWatchSubscription {
    id: String,
    child_id: String,
    child_name: String,
    /// The message count the next event grows from; `None` until the
    /// child's first reachable snapshot arms the baseline (a register
    /// against an unreachable child never fabricates growth).
    baseline: Option<u64>,
    /// The activity label the next status transition compares against;
    /// the register snapshot's own label, so an already-running child
    /// does not fabricate an `idle -> running` event on its first poll.
    last_status: &'static str,
    created_at_ms: u64,
    status: WatchStatus,
    total_events: u64,
    unreachable_polls: u32,
    /// Bumped on every register: a poll observation carries the epoch it
    /// was taken under, so one taken before a re-baseline is discarded
    /// instead of emitting a backwards range against the new baseline.
    epoch: u64,
}

/// The registry caps' refusals (TS text).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum AgentWatchRegisterError {
    ActiveLimit,
    TotalLimit,
}

impl AgentWatchRegisterError {
    pub(crate) fn message(self) -> String {
        match self {
            Self::ActiveLimit => {
                format!("Agent watch limit reached ({AGENT_WATCH_MAX_ACTIVE} active)")
            }
            Self::TotalLimit => {
                format!("Agent watch total limit reached ({AGENT_WATCH_MAX_TOTAL})")
            }
        }
    }
}

/// The per-owner watch registry: subscriptions, the bounded event ring,
/// and the shared poller handle. All mutations go through
/// [`AgentWatchRegistry`]; child snapshots (link round trips) stay
/// outside the lock.
#[derive(Debug)]
pub(crate) struct AgentWatchRegistry {
    subscriptions: HashMap<String, AgentWatchSubscription>,
    total_registered: u64,
    events: VecDeque<RlmWatchEvent>,
    events_bytes: usize,
    dropped: u64,
    next_seq: u64,
    /// The per-registration epoch mint (see
    /// `AgentWatchSubscription::epoch`).
    next_epoch: u64,
    /// Bumped on every poller spawn: a poller exits when its generation
    /// is superseded, so an emptied registry's sleeper never co-polls
    /// with a fresh spawn's poller.
    poll_generation: u64,
    poll: Option<tokio::task::JoinHandle<()>>,
    /// The poll cadence; the `AGENT_WATCH_POLL_INTERVAL_MS` default, a
    /// test knob below.
    poll_interval_ms: u64,
    /// One child snapshot's await bound inside a poll tick; the
    /// `AGENT_WATCH_SNAPSHOT_TIMEOUT_MS` default, a test knob below.
    snapshot_timeout_ms: u64,
}

impl Default for AgentWatchRegistry {
    fn default() -> Self {
        Self {
            subscriptions: HashMap::new(),
            total_registered: 0,
            events: VecDeque::new(),
            events_bytes: 0,
            dropped: 0,
            next_seq: 0,
            next_epoch: 0,
            poll_generation: 0,
            poll: None,
            poll_interval_ms: AGENT_WATCH_POLL_INTERVAL_MS,
            snapshot_timeout_ms: AGENT_WATCH_SNAPSHOT_TIMEOUT_MS,
        }
    }
}

impl AgentWatchRegistry {
    /// The poll cadence the loop reads (at least 1ms so a test knob of 0
    /// can never hot-spin).
    pub(crate) fn poll_interval(&self) -> u64 {
        self.poll_interval_ms.max(1)
    }

    /// One child snapshot's await bound inside a poll tick (at least
    /// 1ms, same hot-spin guard as the cadence).
    pub(crate) fn snapshot_timeout(&self) -> u64 {
        self.snapshot_timeout_ms.max(1)
    }

    fn bump_epoch(&mut self) -> u64 {
        self.next_epoch += 1;
        self.next_epoch
    }

    /// The subscription's current epoch (test seam for scripting stale
    /// observations).
    #[cfg(test)]
    pub(crate) fn subscription_epoch(&self, id: &str) -> u64 {
        self.subscriptions
            .get(id)
            .expect("an epoch is read for a subscription the test registered")
            .epoch
    }

    fn active_count(&self) -> usize {
        self.subscriptions
            .values()
            .filter(|subscription| subscription.status == WatchStatus::Active)
            .count()
    }

    pub(crate) fn has_active(&self) -> bool {
        self.active_count() > 0
    }

    /// Register (or re-baseline) the one watch of a resolved child.
    /// A re-register of a live subscription is the TS
    /// cancel-then-register re-baseline: it consumes no new slot and
    /// never burns the monotonic total cap, and it bumps the epoch so a
    /// poll observation taken under the old registration is discarded.
    /// A row that was cancelled or stopped is a new registration: the
    /// caps apply and the total advances (it never refunds).
    pub(crate) fn register(
        &mut self,
        child_id: &str,
        child_name: &str,
        baseline: Option<(u64, &'static str)>,
        at_ms: u64,
    ) -> Result<RlmAgentWatch, AgentWatchRegisterError> {
        let id = format!("watch-agent-{child_id}");
        let arming_baseline = baseline.map(|(count, _)| count);
        let arming_status = baseline.map_or("idle", |(_, status)| status);
        let already_active = self
            .subscriptions
            .get(&id)
            .is_some_and(|subscription| subscription.status == WatchStatus::Active);
        if already_active {
            let epoch = self.bump_epoch();
            let subscription = self
                .subscriptions
                .get_mut(&id)
                .expect("the active row was just checked");
            subscription.baseline = arming_baseline;
            subscription.last_status = arming_status;
            subscription.created_at_ms = at_ms;
            subscription.unreachable_polls = 0;
            subscription.epoch = epoch;
            return Ok(self.row(&id));
        }
        if self.active_count() >= AGENT_WATCH_MAX_ACTIVE {
            return Err(AgentWatchRegisterError::ActiveLimit);
        }
        if self.total_registered >= AGENT_WATCH_MAX_TOTAL {
            return Err(AgentWatchRegisterError::TotalLimit);
        }
        self.total_registered += 1;
        let epoch = self.bump_epoch();
        self.subscriptions.insert(
            id.clone(),
            AgentWatchSubscription {
                id: id.clone(),
                child_id: child_id.to_string(),
                child_name: child_name.to_string(),
                baseline: arming_baseline,
                last_status: arming_status,
                created_at_ms: at_ms,
                status: WatchStatus::Active,
                total_events: 0,
                unreachable_polls: 0,
                epoch,
            },
        );
        Ok(self.row(&id))
    }

    /// Idempotent cancel: only an `Active` subscription flips and
    /// answers `true`. Published events stay readable (nano-rlm).
    pub(crate) fn cancel(&mut self, id: &str) -> bool {
        self.flip(id, WatchStatus::Cancelled)
    }

    /// A vanished child ends its subscription without an event (TS: "a
    /// vanished child stops emitting; the registry keeps the baseline").
    /// An observation from before a re-baseline is stale: its child was
    /// resolved under the old registration and must not stop the new
    /// one.
    pub(crate) fn mark_vanished(&mut self, id: &str, epoch: u64) {
        match self.subscriptions.get_mut(id) {
            Some(subscription)
                if subscription.status == WatchStatus::Active && subscription.epoch == epoch =>
            {
                subscription.status = WatchStatus::Stopped;
            }
            _ => {}
        }
    }

    /// Owner teardown: every subscription ends cancelled.
    pub(crate) fn cancel_all(&mut self) {
        for subscription in self.subscriptions.values_mut() {
            subscription.status = WatchStatus::Cancelled;
        }
    }

    fn flip(&mut self, id: &str, status: WatchStatus) -> bool {
        match self.subscriptions.get_mut(id) {
            Some(subscription) if subscription.status == WatchStatus::Active => {
                subscription.status = status;
                true
            }
            _ => false,
        }
    }

    /// Apply one child snapshot: quiet unless the count grew or the
    /// activity label changed. An unreachable child accumulates a
    /// dead-poll streak and, past the give-up bound, ends the
    /// subscription with one `-> unreachable` status event.
    pub(crate) fn apply_snapshot(
        &mut self,
        id: &str,
        epoch: u64,
        snapshot: &Result<(u64, &'static str)>,
        at_ms: u64,
    ) -> Option<RlmWatchEvent> {
        let subscription = self.subscriptions.get_mut(id)?;
        if subscription.status != WatchStatus::Active {
            return None;
        }
        if subscription.epoch != epoch {
            return None; // stale: taken under a superseded registration
        }
        // An unreachable child accumulates a dead-poll streak and, past
        // the give-up bound, ends the subscription with one terminal
        // `-> unreachable` status event.
        let Ok((count, status)) = snapshot else {
            subscription.unreachable_polls += 1;
            if subscription.unreachable_polls < WATCH_SUB_MAX_UNREACHABLE_POLLS {
                return None;
            }
            subscription.status = WatchStatus::Stopped;
            return Some(Self::build_event(
                subscription,
                subscription.baseline.unwrap_or_default(),
                subscription.baseline.unwrap_or_default(),
                Some((subscription.last_status, "unreachable")),
                at_ms,
            ));
        };
        let (count, status) = (*count, *status);
        subscription.unreachable_polls = 0;
        // The arming read: a register against an unreachable child never
        // fabricates a 0..count growth event.
        let Some(baseline) = subscription.baseline else {
            subscription.baseline = Some(count);
            subscription.last_status = status;
            return None;
        };
        if count == baseline && status == subscription.last_status {
            return None; // no growth, no repeat status event: quiet
        }
        let change =
            (status != subscription.last_status).then_some((subscription.last_status, status));
        let event = Self::build_event(subscription, baseline, count, change, at_ms);
        subscription.baseline = Some(count);
        subscription.last_status = status;
        Some(event)
    }

    fn build_event(
        subscription: &mut AgentWatchSubscription,
        start: u64,
        end: u64,
        status: Option<(&'static str, &'static str)>,
        at_ms: u64,
    ) -> RlmWatchEvent {
        subscription.total_events += 1;
        RlmWatchEvent {
            id: format!("watch_{}", uuid::Uuid::new_v4().simple()),
            seq: 0, // assigned by push_event
            subscription_id: subscription.id.clone(),
            kind: "watch.agent",
            content: RlmWatchAgentContent {
                target: subscription.child_id.clone(),
                start,
                end,
                status: status.map(|(from, to)| RlmWatchStatusChange { from, to }),
            },
            at_ms,
        }
    }

    /// Append one event to the bounded ring (drop-oldest past either
    /// bound) and answer the event with its assigned `seq`.
    pub(crate) fn push_event(&mut self, mut event: RlmWatchEvent) -> RlmWatchEvent {
        self.next_seq += 1;
        event.seq = self.next_seq;
        self.events_bytes += event_wire_size(&event);
        self.events.push_back(event.clone());
        while self.events.len() > AGENT_WATCH_EVENT_RING_MAX
            || self.events_bytes > AGENT_AGENT_WATCH_EVENT_RING_MAX_BYTES
        {
            match self.events.pop_front() {
                Some(dropped) => {
                    self.events_bytes = self.events_bytes.saturating_sub(event_wire_size(&dropped));
                    self.dropped += 1;
                }
                None => break,
            }
        }
        event
    }

    /// One readable page: the events with `seq > since_seq`, the cursor
    /// the next read passes, and the ring's cumulative drop count.
    /// A FULL page means unread events may remain beyond it: the cursor
    /// is the last event examined, never the ring's tail (which would
    /// skip them). A partial page has every event past `since_seq` in
    /// hand, so the tail is the resume point.
    pub(crate) fn read(&self, since_seq: u64, limit: u64) -> RlmWatchReadPage {
        let page_limit = limit.min(AGENT_WATCH_READ_LIMIT_MAX);
        let events: Vec<RlmWatchEvent> = self
            .events
            .iter()
            .filter(|event| event.seq > since_seq)
            .take(page_limit as usize)
            .cloned()
            .collect();
        let next_seq = if events.len() as u64 >= page_limit {
            events.last().map_or(self.next_seq, |event| event.seq)
        } else {
            self.next_seq
        };
        RlmWatchReadPage {
            events,
            next_seq,
            dropped: self.dropped,
        }
    }

    /// The owner's watch rows, oldest first (stable replies).
    pub(crate) fn rows(&self) -> Vec<RlmAgentWatch> {
        let mut rows: Vec<RlmAgentWatch> =
            self.subscriptions.keys().map(|id| self.row(id)).collect();
        rows.sort_by(|a, b| {
            a.created_at_ms
                .cmp(&b.created_at_ms)
                .then_with(|| a.id.cmp(&b.id))
        });
        rows
    }

    fn row(&self, id: &str) -> RlmAgentWatch {
        let subscription = self
            .subscriptions
            .get(id)
            .expect("a row is read from the same registry that holds it");
        RlmAgentWatch {
            id: subscription.id.clone(),
            child_id: subscription.child_id.clone(),
            child_name: subscription.child_name.clone(),
            messages: subscription.baseline.unwrap_or_default(),
            status: subscription.status.as_str(),
            total_events: subscription.total_events,
            created_at_ms: subscription.created_at_ms,
        }
    }

    /// Test knob: shorten the poll cadence (the 5s default is TS parity).
    #[cfg(test)]
    pub(crate) fn set_poll_interval_ms(&mut self, interval_ms: u64) {
        self.poll_interval_ms = interval_ms;
    }

    /// Test knob: shorten the snapshot await bound (the 5s default keeps
    /// one dead link from withholding a healthy sibling's batch).
    #[cfg(test)]
    pub(crate) fn set_snapshot_timeout_ms(&mut self, timeout_ms: u64) {
        self.snapshot_timeout_ms = timeout_ms;
    }
}

/// The serialized footprint of one ring event (the ring's byte bound).
fn event_wire_size(event: &RlmWatchEvent) -> usize {
    serde_json::to_string(event)
        .map(|wire| wire.len())
        .unwrap_or_default()
}

/// One poll observation: a snapshot result, or the vanished-child arm.
/// Both carry the registration epoch the observation was taken under, so
/// a re-baseline discards one taken before it.
enum WatchObservation {
    Snapshot(String, u64, Result<(u64, &'static str)>),
    Vanished(String, u64),
}

impl SupervisorChildSessionsInner {
    /// `rlm.watch.agent`: resolve the target against the live roster
    /// (child id first, then session name — the roster selector order),
    /// baseline off one child snapshot, and register. An unmatched
    /// target is a hard error, never an empty watch.
    pub(super) async fn watch_agent_register(&self, target: &str) -> Result<RlmAgentWatch> {
        let Some(record) = self.find_record(target).await else {
            bail!("No direct child matches \"{target}\"");
        };
        let (child_id, child_name, active_session_id) = {
            let record = record.lock().await;
            (
                record.rlm_child_id.clone(),
                record.session_name.clone(),
                record.active_session_id.clone(),
            )
        };
        let baseline = self.child_watch_snapshot(&active_session_id).await.ok();
        let at_ms = now_ms();
        let mut registry = self.agent_watch.lock().await;
        registry
            .register(&child_id, &child_name, baseline, at_ms)
            .map_err(|error| anyhow!(error.message()))
    }

    /// Owner teardown: cancel every subscription, retire the poller
    /// generation (a live poller exits at its next tick), and abort a
    /// parked poller outright so nothing holds the runtime open for a
    /// dead session's watches.
    pub(super) async fn shutdown_agent_watch(&self) {
        let poller = {
            let mut registry = self.agent_watch.lock().await;
            registry.cancel_all();
            registry.poll_generation += 1;
            registry.poll.take()
        };
        if let Some(poller) = poller {
            poller.abort();
        }
    }
}

/// Start the shared poller if none is running (the first successful
/// subscribe is the only caller). Generation-keyed: an emptied registry's
/// winding-down poller never co-polls with a fresh one.
pub(super) async fn ensure_agent_watch_poller(inner: &Arc<SupervisorChildSessionsInner>) {
    let generation = {
        let mut registry = inner.agent_watch.lock().await;
        if registry
            .poll
            .as_ref()
            .is_some_and(|poller| !poller.is_finished())
        {
            return;
        }
        registry.poll_generation += 1;
        registry.poll_generation
    };
    let poller = tokio::spawn(agent_watch_poll_loop(Arc::clone(inner), generation));
    let mut registry = inner.agent_watch.lock().await;
    if registry.poll_generation == generation {
        registry.poll = Some(poller);
    }
}

/// The shared poller: one loop for every active subscription of one
/// owner. Each tick snapshots each watched child over the supervisor
/// link (outside the registry lock), applies the results under one lock
/// hold, and delivers one coalesced quiet wake row for the whole batch.
async fn agent_watch_poll_loop(inner: Arc<SupervisorChildSessionsInner>, generation: u64) {
    loop {
        let interval_ms = inner.agent_watch.lock().await.poll_interval();
        tokio::time::sleep(Duration::from_millis(interval_ms)).await;
        let targets: Vec<(String, u64, String)> = {
            let registry = inner.agent_watch.lock().await;
            if registry.poll_generation != generation || !registry.has_active() {
                return; // superseded, emptied, or torn down: exit quietly
            }
            registry
                .subscriptions
                .values()
                .filter(|subscription| subscription.status == WatchStatus::Active)
                .map(|subscription| {
                    (
                        subscription.id.clone(),
                        subscription.epoch,
                        subscription.child_id.clone(),
                    )
                })
                .collect()
        };
        // Child resolution and snapshots are link round trips: never
        // hold the registry lock across them. A chunk's round trips
        // proceed concurrently (a healthy child's event is not withheld
        // behind an unresponsive sibling's 30s link timeout), each
        // bounded by the snapshot timeout; the chunk order keeps the
        // observations (and so the ring) in target order.
        let snapshot_timeout_ms = inner.agent_watch.lock().await.snapshot_timeout();
        let mut observations = Vec::with_capacity(targets.len());
        for chunk in targets.chunks(AGENT_WATCH_SNAPSHOT_CONCURRENCY) {
            let results = futures::future::join_all(chunk.iter().map(
                |(subscription_id, epoch, child_id)| async {
                    let Some(record) = inner.find_record(child_id).await else {
                        return WatchObservation::Vanished(subscription_id.clone(), *epoch);
                    };
                    let active_session_id = record.lock().await.active_session_id.clone();
                    let snapshot = tokio::time::timeout(
                        Duration::from_millis(snapshot_timeout_ms),
                        inner.child_watch_snapshot(&active_session_id),
                    )
                    .await
                    .unwrap_or_else(|_| Err(anyhow!("agent watch snapshot timed out")));
                    WatchObservation::Snapshot(subscription_id.clone(), *epoch, snapshot)
                },
            ))
            .await;
            observations.extend(results);
        }
        // Apply under one hold: events land in the ring in observation
        // order, and the wake row's lines read each subscription's row
        // name from the same lock.
        let lines: Vec<(RlmWatchEvent, String)> = {
            let mut registry = inner.agent_watch.lock().await;
            if registry.poll_generation != generation {
                return;
            }
            let mut lines = Vec::new();
            for observation in observations {
                match observation {
                    WatchObservation::Vanished(subscription_id, epoch) => {
                        registry.mark_vanished(&subscription_id, epoch);
                    }
                    WatchObservation::Snapshot(subscription_id, epoch, snapshot) => {
                        if let Some(event) =
                            registry.apply_snapshot(&subscription_id, epoch, &snapshot, now_ms())
                        {
                            let event = registry.push_event(event);
                            let child_name = registry
                                .subscriptions
                                .get(&event.subscription_id)
                                .map(|subscription| subscription.child_name.clone())
                                .unwrap_or_default();
                            lines.push((event, child_name));
                        }
                    }
                }
            }
            lines
        };
        if !lines.is_empty() {
            let notice_lines: Vec<AgentWatchNoticeLine> = lines
                .iter()
                .map(|(event, child_name)| AgentWatchNoticeLine {
                    subscription_id: &event.subscription_id,
                    child_id: &event.content.target,
                    child_name,
                    from_index: event.content.start,
                    to_index: event.content.end,
                    status: event
                        .content
                        .status
                        .as_ref()
                        .map(|change| (change.from, change.to)),
                })
                .collect();
            inner
                .deliver_terminal_notice(create_agent_watch_notice(&notice_lines, now_ms()))
                .await;
        }
    }
}
