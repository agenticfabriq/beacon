"""A stable identity for a run's configuration.

Two runs belong in the same row of a benchmark table when they were produced by
the same system, version, model and knobs. Nothing could decide that: the model
sat inside a JSONB blob and the knobs had no identity at all, so a results
matrix could not form its rows.

The digest is over the configuration that changes what a run measures. Secrets
are excluded -- rotating a key must not split a row in two -- and keys are
sorted so the digest does not depend on serialisation order.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

# Excluded from the digest: a credential reference says nothing about what was
# measured, and rotating one would otherwise look like a new configuration.
#
# The provenance keys are excluded for the same reason and a sharper one. They
# record WHERE a run's record came from -- which file, which revision, which
# runner -- and they vary between two runs of the very same configuration by
# construction: re-run a config and the report file's digest changes. A
# digest that included them would give every repeat a new identity, so
# replicates would stop pooling into one row and the matrix's error bar (the
# spread across a row's runs) would silently die -- the feature and the trap
# arrive together. The rule: **the digest covers what was CONFIGURED, never
# how the record was MADE.**
#
# ``source_rev`` is the sharpest of them and the argument for excluding it is
# NOT that the engine build is irrelevant -- it is that the build is a fact
# about the SYSTEM, and beacon models the system in ``Solution.version``, not
# in a config knob. Digesting it would mislabel a system fact as a knob, and
# it would split genuine replicates on a missing stamp: the first imported
# Spider run predates the field entirely, so an absent stamp would read as a
# different engine when it only means an unrecorded one. The real gap it
# exposes lives elsewhere and is open -- an in-process SUT hardcodes its
# VERSION, so two genuinely different engine builds register as one solution
# version and DO merge. That must be fixed where version is declared.
#
# These names are excluded from EVERY config, not only an importer's, so they
# are reserved: a SUT must not use one of them for a knob that changes what a
# run measures.
EXCLUDED_KEYS = frozenset(
    {
        "secret_refs",
        "imported_from",
        "imported_sha256",
        "source_runner",
        "source_rev",
    }
)
# Also excluded: bookkeeping a loader attaches to a run, which varies between two
# runs of the very same configuration and says nothing about what was measured.
#
# `config_label` is deliberately NOT excluded. It looks like a display name, but
# a runner's captured config does not always cover every knob it turned --
# constrained decoding, for instance, changes what a run measures and appears in
# no field here. When two runs carry identical config and different labels, the
# label is the only remaining evidence that they are different experiments, and
# the runner is the authority on that. Excluding it merged seven real
# configurations of one model into a single row.
EXCLUDED_EXTRAS = frozenset({"source_report", "run_tokens", "llm_calls"})


def _canonical(config: Mapping[str, Any]) -> dict[str, Any]:
    canonical: dict[str, Any] = {}
    for key, value in config.items():
        if key in EXCLUDED_KEYS:
            continue
        if key == "extras" and isinstance(value, dict):
            extras = {k: v for k, v in value.items() if k not in EXCLUDED_EXTRAS}
            if extras:
                canonical[key] = extras
            continue
        canonical[key] = value
    return canonical


def config_digest(config: Mapping[str, Any]) -> str:
    """Return a stable digest of the configuration a run was produced under."""
    canonical = _canonical(config)
    encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def model_id_of(config: Mapping[str, Any]) -> str | None:
    """Return the model a config names, or None when it names none."""
    value = config.get("model_id")
    return str(value) if value else None


def config_label_of(config: Mapping[str, Any]) -> str | None:
    """Return the runner-supplied label for this configuration, if any."""
    extras = config.get("extras")
    if isinstance(extras, dict):
        label = extras.get("config_label")
        if label:
            return str(label)
    return None
