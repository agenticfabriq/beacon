"""What makes two runs the same configuration.

A benchmark row is (model x config), and the grouping has to be exactly right:
too loose and two different experiments merge into one row, too tight and every
run becomes its own row and nothing is ever compared.
"""

from __future__ import annotations

from beacon_runner.config_identity import config_digest, config_label_of, model_id_of


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


def test_the_label_does_not_decide_identity() -> None:
    """Two names for the same knobs are one configuration, not two."""
    labelled = _config(extras={"config_label": "+guided_json"})

    assert config_digest(labelled) == config_digest(_config())


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
