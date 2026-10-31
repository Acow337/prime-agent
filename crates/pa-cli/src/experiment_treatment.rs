//! Experiment-only Harness treatment selection for headless runs.
//!
//! The product CLI deliberately has no treatment flag: normal sessions always
//! run the full product. A controlled evaluation harness sets
//! `PRIME_AGENT_HARNESS_TREATMENT` to one of the eight stable factor IDs
//! (`r1-s1-m1` .. `r0-s0-m0`); an unknown value fails the run instead of
//! silently falling back to the full product.

use pa_core::session_engine::engine::{SessionEngine, SessionEngineConfig};
use pa_core::session_engine::treatment::HarnessTreatment;

/// Environment variable naming the experiment treatment.
pub const ENV_HARNESS_TREATMENT: &str = "PRIME_AGENT_HARNESS_TREATMENT";

/// Resolve the treatment from one optional value (the env var's content).
///
/// # Errors
///
/// Returns an error when the value is not one of the eight stable IDs.
pub fn parse_treatment(value: Option<&str>) -> anyhow::Result<HarnessTreatment> {
    match value.map(str::trim).filter(|value| !value.is_empty()) {
        None => Ok(HarnessTreatment::FULL),
        Some(id) => HarnessTreatment::from_id(id).ok_or_else(|| {
            anyhow::anyhow!(
                "{ENV_HARNESS_TREATMENT} must be one of r1-s1-m1 .. r0-s0-m0, got {id:?}"
            )
        }),
    }
}

/// The treatment the current process was started with.
///
/// # Errors
///
/// Returns an error for an unknown `PRIME_AGENT_HARNESS_TREATMENT` value.
pub fn from_env() -> anyhow::Result<HarnessTreatment> {
    parse_treatment(std::env::var(ENV_HARNESS_TREATMENT).ok().as_deref())
}

/// Build a session under the configured experiment treatment.
///
/// # Errors
///
/// Returns an error for an invalid treatment value or for any session build
/// failure.
pub async fn create_session(config: SessionEngineConfig) -> anyhow::Result<SessionEngine> {
    let treatment = from_env()?;
    if treatment == HarnessTreatment::FULL {
        return pa_core::session_engine::engine::create_session(config).await;
    }
    pa_core::session_engine::engine::create_session_with_treatment(config, treatment).await
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn absent_or_blank_means_the_full_product() {
        assert_eq!(parse_treatment(None).unwrap(), HarnessTreatment::FULL);
        assert_eq!(parse_treatment(Some("  ")).unwrap(), HarnessTreatment::FULL);
    }

    #[test]
    fn every_stable_id_is_accepted() {
        for id in [
            "r1-s1-m1", "r1-s1-m0", "r1-s0-m1", "r1-s0-m0", "r0-s1-m1", "r0-s1-m0", "r0-s0-m1",
            "r0-s0-m0",
        ] {
            assert_eq!(parse_treatment(Some(id)).unwrap().id(), id);
        }
    }

    #[test]
    fn unknown_values_fail_loudly() {
        for invalid in ["r2-s1-m1", "full", "r1-s1", "R1-S1-M1"] {
            assert!(parse_treatment(Some(invalid)).is_err(), "{invalid:?}");
        }
    }
}
