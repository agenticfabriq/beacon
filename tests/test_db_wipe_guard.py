"""The test-database guard must refuse the database it exists to protect (B9).

Every package conftest runs ``DROP SCHEMA public CASCADE`` before and after its
engine fixture. The guard previously admitted any name containing "beacon",
which is the dev database's entire name, so ``make test`` wiped real run
history and the guard raised nothing.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_root_conftest() -> object:
    """Import the repo-root conftest by path.

    A bare ``import conftest`` resolves to whichever package conftest pytest
    loaded most recently, which is not this one.
    """
    path = Path(__file__).resolve().parents[1] / "conftest.py"
    spec = importlib.util.spec_from_file_location("beacon_root_conftest", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_assert_safe_test_database_url = _load_root_conftest()._assert_safe_test_database_url  # noqa: SLF001

_DEV = "postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon"
_TEST = "postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon_test"
_CI = "postgresql+psycopg://postgres@localhost:5432/beacon_ci"


@pytest.mark.parametrize("url", [_TEST, _CI])
def test_disposable_databases_are_allowed(url: str) -> None:
    _assert_safe_test_database_url(url)


@pytest.mark.parametrize(
    "url",
    [
        _DEV,
        "postgresql+psycopg://beacon:beacon_dev@localhost:5432/beacon_dev",
        "postgresql://u@h/production",
        "postgresql://u@h/beacon_prod",
    ],
)
def test_databases_that_must_survive_are_refused(url: str) -> None:
    with pytest.raises(RuntimeError, match="Refusing to run tests"):
        _assert_safe_test_database_url(url)


def test_the_refusal_names_a_usable_alternative() -> None:
    with pytest.raises(RuntimeError, match="beacon_test"):
        _assert_safe_test_database_url(_DEV)


def test_the_override_is_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEACON_ALLOW_TEST_DB_WIPE", "1")
    _assert_safe_test_database_url(_DEV)


def test_a_urlless_value_is_not_second_guessed() -> None:
    """No database name to judge means the fixtures have nothing to drop."""
    _assert_safe_test_database_url("postgresql://host")
