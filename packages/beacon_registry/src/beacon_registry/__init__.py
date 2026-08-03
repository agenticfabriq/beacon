"""Benchmark suites: the container runs are compared within.

What remains of this package after curation moved to the semantic layer. The
name is now wider than the contents -- suites are the only thing here -- but
renaming the package is churn for its own sake and can wait for the
benchmark-first UI work.

Curation (item tiers, promotion from production traces, review queues,
selection heuristics) and provenance are deliberately absent: the semantic
layer curates gold and audits it through GoldenQuestion history, and beacon
consumes an export rather than maintaining a worse copy. See
beacon-internal docs/2026-08-02-beacon-verity-boundary.md.
"""

from beacon_registry.errors import (
    BeaconRegistryError,
    DuplicateSuiteError,
    SuiteNotFoundError,
)
from beacon_registry.suites import SuiteService

__all__ = [
    "BeaconRegistryError",
    "DuplicateSuiteError",
    "SuiteNotFoundError",
    "SuiteService",
]
