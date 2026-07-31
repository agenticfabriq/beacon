from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from beacon_benchmarks.bird_minidev.engine import make_bird_engine_factory
from beacon_runner.types import EvalItem


def _item(db_id: str) -> EvalItem:
    return EvalItem(
        item_id=f"item-{db_id}",
        suite="bird_minidev_v2",
        query={"db_id": db_id, "question": "Q"},
        ground_truth={"sql": "SELECT 1"},
        metadata={},
    )


def test_factory_caches_engine_per_schema() -> None:
    factory = make_bird_engine_factory("postgresql+psycopg://u:p@h/db")
    with (
        patch("beacon_benchmarks.bird_minidev.engine.make_engine") as make_engine_mock,
        patch("beacon_benchmarks.bird_minidev.engine.sa_event") as sa_event,
    ):
        make_engine_mock.side_effect = lambda url: MagicMock(name=f"engine-{url}")
        sa_event.listens_for.side_effect = lambda *_args, **_kwargs: lambda fn: fn
        e1 = factory(_item("california_schools"))
        e2 = factory(_item("california_schools"))
        e3 = factory(_item("card_games"))
        assert e1 is e2
        assert e3 is not e1
        assert make_engine_mock.call_count == 2


def test_factory_listener_sets_search_path_on_connect() -> None:
    factory = make_bird_engine_factory("postgresql+psycopg://u:p@h/db")
    captured_sql: list[str] = []

    class _Cursor:
        def execute(self, sql: str) -> None:
            captured_sql.append(sql)

        def close(self) -> None:
            return None

    class _Conn:
        def cursor(self) -> _Cursor:
            return _Cursor()

    listeners: list[tuple[str, Any]] = []

    def _listens_for(_target: Any, identifier: str) -> Any:
        def deco(fn: Any) -> Any:
            listeners.append((identifier, fn))
            return fn

        return deco

    with (
        patch("beacon_benchmarks.bird_minidev.engine.make_engine") as make_engine_mock,
        patch("beacon_benchmarks.bird_minidev.engine.sa_event") as sa_event,
    ):
        make_engine_mock.return_value = MagicMock()
        sa_event.listens_for.side_effect = _listens_for
        factory(_item("superhero"))
        connect_fn = next(fn for ident, fn in listeners if ident == "connect")
        connect_fn(_Conn(), None)

    assert captured_sql == ['SET search_path TO "bird_superhero", public']


def test_factory_requires_db_id_in_query() -> None:
    factory = make_bird_engine_factory("postgresql+psycopg://u:p@h/db")
    bad_item = EvalItem(
        item_id="x", suite="s", query={}, ground_truth={"sql": "SELECT 1"}, metadata={}
    )
    with pytest.raises(KeyError):
        factory(bad_item)
