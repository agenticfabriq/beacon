"""Tests for the LOO config enumerator."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from beacon_ablation.ablator import Ablator
from beacon_ablation.errors import InvalidConfigurationError, LayerNotDeclaredError


class _FakeSut:
    def __init__(self, layer_names: list[str]) -> None:
        self._layer_names = layer_names

    def layers(self) -> list[SimpleNamespace]:
        return [SimpleNamespace(name=name) for name in self._layer_names]


class _FakeConfig:
    def __init__(
        self,
        layers_enabled: dict[str, bool],
        model_id: str = "m",
        prompt_version: str = "p",
    ) -> None:
        self.layers_enabled = dict(layers_enabled)
        self.model_id = model_id
        self.prompt_version = prompt_version

    def model_copy(self, *, deep: bool = False) -> _FakeConfig:
        return _FakeConfig(
            layers_enabled=dict(self.layers_enabled),
            model_id=self.model_id,
            prompt_version=self.prompt_version,
        )


def test_loo_produces_baseline_plus_n_configs() -> None:
    sut = _FakeSut(["ontology", "retry_loop"])
    base = _FakeConfig({"ontology": True, "retry_loop": True})

    out = Ablator().enumerate_loo_configs(base, sut=sut)

    assert [label for label, _ in out] == ["baseline", "no_ontology", "no_retry_loop"]


def test_loo_baseline_unchanged() -> None:
    sut = _FakeSut(["a", "b"])
    base = _FakeConfig({"a": True, "b": True})

    label, config = Ablator().enumerate_loo_configs(base, sut=sut)[0]

    assert label == "baseline"
    assert config.layers_enabled == {"a": True, "b": True}


def test_loo_each_ablated_flips_one_layer() -> None:
    sut = _FakeSut(["a", "b", "c"])
    base = _FakeConfig({"a": True, "b": True, "c": True})

    out = Ablator().enumerate_loo_configs(base, sut=sut)
    config_by_label = dict(out)

    assert config_by_label["no_a"].layers_enabled == {"a": False, "b": True, "c": True}
    assert config_by_label["no_b"].layers_enabled == {"a": True, "b": False, "c": True}
    assert config_by_label["no_c"].layers_enabled == {"a": True, "b": True, "c": False}


def test_loo_skips_already_disabled_layers() -> None:
    sut = _FakeSut(["a", "b"])
    base = _FakeConfig({"a": False, "b": True})

    out = Ablator().enumerate_loo_configs(base, sut=sut)

    assert [label for label, _ in out] == ["baseline", "no_b"]


def test_loo_zero_enabled_layers_returns_baseline_only() -> None:
    sut = _FakeSut(["a", "b"])
    base = _FakeConfig({"a": False, "b": False})

    out = Ablator().enumerate_loo_configs(base, sut=sut)

    assert [label for label, _ in out] == ["baseline"]


def test_loo_rejects_layer_not_declared_by_sut() -> None:
    sut = _FakeSut(["ontology"])
    base = _FakeConfig({"ontology": True, "nonexistent": True})

    with pytest.raises(LayerNotDeclaredError, match="nonexistent"):
        Ablator().enumerate_loo_configs(base, sut=sut)


def test_loo_rejects_empty_config() -> None:
    sut = _FakeSut(["a"])
    base = _FakeConfig({})

    with pytest.raises(InvalidConfigurationError, match="no layers"):
        Ablator().enumerate_loo_configs(base, sut=sut)


def test_loo_does_not_mutate_input_config() -> None:
    sut = _FakeSut(["a"])
    base = _FakeConfig({"a": True})

    out = Ablator().enumerate_loo_configs(base, sut=sut)
    _, ablated = out[1]
    ablated.layers_enabled["a"] = True

    assert base.layers_enabled == {"a": True}
