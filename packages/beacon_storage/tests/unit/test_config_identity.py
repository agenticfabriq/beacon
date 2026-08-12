"""What makes two runs the same configuration.

A benchmark row is (model x config), and the grouping has to be exactly right:
too loose and two different experiments merge into one row, too tight and every
run becomes its own row and nothing is ever compared.
"""

from __future__ import annotations

from beacon_storage.config_identity import config_digest, config_label_of, model_id_of


def _config(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "model_id": "qwen2.5-coder-14b",
        "prompt_version": "v0",
        "layers_enabled": {"verifier": True, "self_consistency": False},
        "secret_refs": {"openai": "vault://a"},
        "extras": {},
    }
    base.update(over)
    return base


def test_the_same_configuration_digests_the_same() -> None:
    assert config_digest(_config()) == config_digest(_config())


def test_key_order_does_not_change_the_digest() -> None:
    """Otherwise the digest would depend on how the config was serialised."""
    reordered = dict(reversed(list(_config().items())))

    assert config_digest(reordered) == config_digest(_config())


def test_a_different_model_is_a_different_configuration() -> None:
    assert config_digest(_config(model_id="qwen2.5-coder-32b")) != config_digest(_config())


def test_a_different_layer_setting_is_a_different_configuration() -> None:
    changed = _config(layers_enabled={"verifier": False, "self_consistency": False})

    assert config_digest(changed) != config_digest(_config())


def test_a_different_prompt_version_is_a_different_configuration() -> None:
    assert config_digest(_config(prompt_version="v1")) != config_digest(_config())


def test_rotating_a_secret_does_not_split_a_row() -> None:
    """A credential reference says nothing about what was measured."""
    rotated = _config(secret_refs={"openai": "vault://b"})

    assert config_digest(rotated) == config_digest(_config())


def test_the_label_is_part_of_the_identity() -> None:
    """A runner's captured config does not always cover every knob it turned.

    Constrained decoding changes what a run measures and appears in no field
    here, so two runs with identical config and different labels are different
    experiments. Treating the label as cosmetic merged baseline, +guided_json,
    +assertive and +verify for one model into a single row.
    """
    labelled = _config(extras={"config_label": "+guided_json"})
    other = _config(extras={"config_label": "baseline"})

    assert config_digest(labelled) != config_digest(other)
    assert config_digest(labelled) != config_digest(_config())


def test_loader_bookkeeping_does_not_split_a_row() -> None:
    """Token totals and the source file vary between runs of one configuration."""
    with_bookkeeping = _config(
        extras={"source_report": "a.jsonl", "run_tokens": 12, "llm_calls": 3}
    )

    assert config_digest(with_bookkeeping) == config_digest(_config())


def test_a_meaningful_extra_still_counts() -> None:
    """Only the known bookkeeping keys are ignored, not everything under extras."""
    tuned = _config(extras={"temperature": 0.7})

    assert config_digest(tuned) != config_digest(_config())


def test_the_model_is_read_out_of_the_config() -> None:
    assert model_id_of(_config()) == "qwen2.5-coder-14b"


def test_a_config_naming_no_model_yields_none() -> None:
    """Unknown must stay distinguishable from any value we could invent."""
    assert model_id_of({"prompt_version": "v0"}) is None
    assert model_id_of({"model_id": ""}) is None


def test_the_label_is_read_out_of_extras() -> None:
    assert config_label_of(_config(extras={"config_label": "+guided"})) == "+guided"
    assert config_label_of(_config()) is None


def test_provenance_does_not_change_a_configuration_s_identity() -> None:
    """Re-running a config produces a new report file, so its digest changes --
    and if that reached the identity, every replicate would land in its own
    matrix row. Repetition is how a row learns its error bar, so provenance
    must not split one: the digest covers what was CONFIGURED, never how the
    record was MADE."""
    configured = {"model_id": "gpt-5.5", "retrieval_k": 24, "candidates": 1}
    first = {
        **configured,
        "imported_from": "spider2-results.jsonl",
        "imported_sha256": "5a91bab93b23",
        "source_runner": "mnemiq scripts/run_spider2.py",
        "source_rev": "2ba8894",
    }
    replicate = {
        **configured,
        "imported_from": "spider2-results.jsonl",
        "imported_sha256": "0000deadbeef",  # a second run of the same config
        "source_runner": "mnemiq scripts/run_spider2.py",
        # A stamp the earlier run does not carry at all. An absent stamp means
        # an UNRECORDED engine build, never a different one -- splitting on it
        # would break a real replicate pair. A genuinely different build must
        # separate through Solution.version, which is where the system's
        # identity lives; that gap is open and does not belong in this digest.
        "source_rev": "d85fd04",
    }

    assert config_digest(first) == config_digest(replicate)
    assert config_digest(first) == config_digest(configured)


def test_a_knob_that_changes_what_is_measured_does_change_identity() -> None:
    """The other half of the same rule: retrieval_k=12 and retrieval_k=24 are
    different experiments and must never pool into one row."""
    k12 = {"model_id": "gpt-5.5", "retrieval_k": 12}
    k24 = {"model_id": "gpt-5.5", "retrieval_k": 24}

    assert config_digest(k12) != config_digest(k24)


def test_a_knob_change_still_splits_when_provenance_moves_with_it() -> None:
    """The two rules together, which is how they actually arrive: a real knob
    change lands in a new run, from a new file, at a new revision. The
    provenance must not mask the knob."""
    k12 = {
        "model_id": "gpt-5.5",
        "retrieval_k": 12,
        "imported_sha256": "aaaa",
        "source_rev": "97ff945",
    }
    k24 = {
        "model_id": "gpt-5.5",
        "retrieval_k": 24,
        "imported_sha256": "bbbb",
        "source_rev": "d85fd04",
    }

    assert config_digest(k12) != config_digest(k24)


def test_the_import_paths_own_semantics_knobs_split_a_row() -> None:
    """--strict is not a label, it changes what PASS means: the headline
    metric and the derivation both move. Two imports that disagree about the
    definition of correct must never average into one number (the defect that
    put two grading semantics in one row once already)."""
    facts = {
        "executor": "sqlite-native",
        "headline_metric": "got_facts",
        "pass_semantics": "derived from beacon got_facts",
        "imported_sha256": "aaaa",
    }
    strict = {
        "executor": "sqlite-native",
        "headline_metric": "exact_match",
        "pass_semantics": "derived from beacon exact_match",
        "imported_sha256": "bbbb",
    }

    assert config_digest(facts) != config_digest(strict)
