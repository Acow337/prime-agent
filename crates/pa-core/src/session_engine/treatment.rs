//! Typed `R/S/M` Harness treatment vocabulary for controlled evaluation runs.
//!
//! The three factors are orthogonal: whether Python values survive a settled
//! turn (`R`), whether the session may create recursive child sessions (`S`),
//! and whether persistent Harness state may be read or written (`M`). Normal
//! product sessions always resolve [`HarnessTreatment::FULL`]; the experiment
//! runner selects a treatment explicitly through
//! `SessionEngine::create_session_with_treatment`.

/// Whether Python values survive from one settled model turn to the next.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum ReplPersistence {
    /// The REPL namespace is kept across settled turns (product default).
    #[default]
    AcrossTurns,
    /// The REPL namespace is cleared once a turn settles.
    ResetAfterTurn,
}

/// Whether this session may create recursive child sessions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SubagentAccess {
    /// `rlm.spawn` and `rlm.create_session` may create descendants (product default).
    #[default]
    Enabled,
    /// Child-session creation is refused at admission.
    Disabled,
}

/// Whether persistent Harness state may be read, injected, or changed.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum HarnessStateAccess {
    /// Harness state is read, injected, and refined (product default).
    #[default]
    Enabled,
    /// Runs against a pinned empty Harness; refinement writes are refused.
    Disabled,
}

/// Orthogonal Harness factors used by controlled evaluation runs.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct HarnessTreatment {
    /// `R`: persistent REPL namespace across settled turns.
    pub repl: ReplPersistence,
    /// `S`: recursive child-session access.
    pub subagents: SubagentAccess,
    /// `M`: persistent Harness state access.
    pub harness_state: HarnessStateAccess,
}

impl HarnessTreatment {
    /// The unchanged full product: every factor enabled.
    pub const FULL: Self = Self {
        repl: ReplPersistence::AcrossTurns,
        subagents: SubagentAccess::Enabled,
        harness_state: HarnessStateAccess::Enabled,
    };

    /// Stable factor ID written into experiment manifests.
    #[must_use]
    pub const fn id(self) -> &'static str {
        match (self.repl, self.subagents, self.harness_state) {
            (
                ReplPersistence::AcrossTurns,
                SubagentAccess::Enabled,
                HarnessStateAccess::Enabled,
            ) => "r1-s1-m1",
            (
                ReplPersistence::AcrossTurns,
                SubagentAccess::Enabled,
                HarnessStateAccess::Disabled,
            ) => "r1-s1-m0",
            (
                ReplPersistence::AcrossTurns,
                SubagentAccess::Disabled,
                HarnessStateAccess::Enabled,
            ) => "r1-s0-m1",
            (
                ReplPersistence::AcrossTurns,
                SubagentAccess::Disabled,
                HarnessStateAccess::Disabled,
            ) => "r1-s0-m0",
            (
                ReplPersistence::ResetAfterTurn,
                SubagentAccess::Enabled,
                HarnessStateAccess::Enabled,
            ) => "r0-s1-m1",
            (
                ReplPersistence::ResetAfterTurn,
                SubagentAccess::Enabled,
                HarnessStateAccess::Disabled,
            ) => "r0-s1-m0",
            (
                ReplPersistence::ResetAfterTurn,
                SubagentAccess::Disabled,
                HarnessStateAccess::Enabled,
            ) => "r0-s0-m1",
            (
                ReplPersistence::ResetAfterTurn,
                SubagentAccess::Disabled,
                HarnessStateAccess::Disabled,
            ) => "r0-s0-m0",
        }
    }

    /// Parse a stable factor ID (the inverse of [`HarnessTreatment::id`]).
    ///
    /// Returns `None` for any string outside the eight documented IDs, so an
    /// experiment harness fails loudly instead of silently running the full
    /// product.
    #[must_use]
    pub fn from_id(id: &str) -> Option<Self> {
        let mut parts = id.split('-');
        let (repl, subagents, harness_state) = (parts.next()?, parts.next()?, parts.next()?);
        if parts.next().is_some() {
            return None;
        }
        Some(Self {
            repl: match repl {
                "r1" => ReplPersistence::AcrossTurns,
                "r0" => ReplPersistence::ResetAfterTurn,
                _ => return None,
            },
            subagents: match subagents {
                "s1" => SubagentAccess::Enabled,
                "s0" => SubagentAccess::Disabled,
                _ => return None,
            },
            harness_state: match harness_state {
                "m1" => HarnessStateAccess::Enabled,
                "m0" => HarnessStateAccess::Disabled,
                _ => return None,
            },
        })
    }
}

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

    #[test]
    fn every_factor_id_round_trips_and_unknown_ids_are_rejected() {
        for repl in [
            ReplPersistence::AcrossTurns,
            ReplPersistence::ResetAfterTurn,
        ] {
            for subagents in [SubagentAccess::Enabled, SubagentAccess::Disabled] {
                for harness_state in [HarnessStateAccess::Enabled, HarnessStateAccess::Disabled] {
                    let treatment = HarnessTreatment {
                        repl,
                        subagents,
                        harness_state,
                    };
                    assert_eq!(
                        HarnessTreatment::from_id(treatment.id()),
                        Some(treatment),
                        "{}",
                        treatment.id()
                    );
                }
            }
        }
        for invalid in [
            "",
            "r1-s1",
            "r1-s1-m1-extra",
            "r2-s1-m1",
            "R1-S1-M1",
            "r1-s1-m2",
        ] {
            assert_eq!(HarnessTreatment::from_id(invalid), None, "{invalid:?}");
        }
    }
}
