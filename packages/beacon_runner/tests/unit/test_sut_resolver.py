"""Resolving a SUT the CLI can run, by entry-point name or import path (B4)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from beacon_runner.dummy_sut import DummySUT
from beacon_runner.errors import SutNotFoundError
from beacon_runner.sut.resolver import (
    list_available_suts,
    load_sut_config,
    resolve_sut_factory,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_bundled_suts_are_discoverable() -> None:
    """`suts available` must enumerate something real, not a hardcoded string."""
    names = list_available_suts()
    assert "dummy" in names
    assert "mnemiq-inprocess" in names


def test_an_entry_point_name_resolves_to_its_class() -> None:
    assert resolve_sut_factory("dummy") is DummySUT


def test_the_mnemiq_sut_resolves_without_mnemiq_installed() -> None:
    """mnemiq is an out-of-band install; the adapter must import regardless."""
    factory = resolve_sut_factory("mnemiq-inprocess")
    assert factory.__name__ == "MnemiqInProcessSUT"


def test_an_import_path_resolves_for_a_sut_beacon_does_not_ship() -> None:
    assert resolve_sut_factory("beacon_runner.dummy_sut:DummySUT") is DummySUT


def test_an_unknown_entry_point_name_lists_what_is_available() -> None:
    with pytest.raises(SutNotFoundError, match="available:"):
        resolve_sut_factory("no-such-sut")


def test_an_unimportable_module_is_reported_clearly() -> None:
    with pytest.raises(SutNotFoundError, match="cannot import module"):
        resolve_sut_factory("beacon_runner.does_not_exist:Thing")


def test_a_missing_attribute_is_reported_clearly() -> None:
    with pytest.raises(SutNotFoundError, match="has no attribute"):
        resolve_sut_factory("beacon_runner.dummy_sut:NotAClass")


def test_a_malformed_import_path_is_rejected() -> None:
    with pytest.raises(SutNotFoundError, match="package.module:Attribute"):
        resolve_sut_factory("beacon_runner.dummy_sut:")


def test_a_non_callable_target_is_rejected() -> None:
    with pytest.raises(SutNotFoundError, match="non-callable"):
        resolve_sut_factory("beacon_runner.dummy_sut:__doc__")


def test_config_keeps_json_types(tmp_path: Path) -> None:
    """A JSON object preserves ints and nesting that k=v strings would flatten."""
    path = tmp_path / "sut.json"
    path.write_text(json.dumps({"candidates": 3, "nested": {"a": [1, 2]}}), encoding="utf-8")

    config = load_sut_config(path)

    assert config["candidates"] == 3
    assert config["nested"] == {"a": [1, 2]}


def test_config_is_empty_when_unset() -> None:
    assert load_sut_config(None) == {}


def test_a_non_object_config_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "sut.json"
    path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    with pytest.raises(SutNotFoundError, match="must be a JSON object"):
        load_sut_config(path)


def test_invalid_json_is_reported_clearly(tmp_path: Path) -> None:
    path = tmp_path / "sut.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(SutNotFoundError, match="not valid JSON"):
        load_sut_config(path)


def test_a_resolved_factory_builds_a_usable_sut() -> None:
    from uuid import uuid4

    factory = resolve_sut_factory("dummy")
    sut = factory(owner_team_id=uuid4())

    assert sut.identity().solution_id == "dummy"
    assert [layer.name for layer in sut.layers()] == ["ontology", "retry_loop"]
