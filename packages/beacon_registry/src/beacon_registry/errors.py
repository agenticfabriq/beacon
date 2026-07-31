"""beacon_registry exception hierarchy."""

from __future__ import annotations


class BeaconRegistryError(Exception):
    code: str = "registry_error"


class InvalidTierTransitionError(BeaconRegistryError):
    code = "invalid_tier_transition"


class EvalItemNotFoundError(BeaconRegistryError):
    code = "eval_item_not_found"


class SuiteNotFoundError(BeaconRegistryError):
    code = "suite_not_found"


class DuplicateSuiteError(BeaconRegistryError):
    code = "duplicate_suite"


class InvalidSelectorConfigError(BeaconRegistryError):
    code = "invalid_selector_config"


class NoCandidateRunsError(BeaconRegistryError):
    code = "no_candidate_runs"


class IngestionAuthError(BeaconRegistryError):
    code = "ingestion_auth_failed"
