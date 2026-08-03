"""beacon_registry exception hierarchy."""

from __future__ import annotations


class BeaconRegistryError(Exception):
    code: str = "registry_error"


class SuiteNotFoundError(BeaconRegistryError):
    code = "suite_not_found"


class DuplicateSuiteError(BeaconRegistryError):
    code = "duplicate_suite"
