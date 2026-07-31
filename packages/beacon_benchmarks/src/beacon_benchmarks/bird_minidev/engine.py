"""Per-`bird_<db_id>` SQLAlchemy engine factory for BIRD eval items."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from beacon_storage.db import make_engine
from sqlalchemy import event as sa_event

if TYPE_CHECKING:
    from collections.abc import Callable

    from beacon_runner.types import EvalItem
    from sqlalchemy.engine import Engine


def make_bird_engine_factory(beacon_db_url: str) -> Callable[[EvalItem], Engine]:
    """Return an engine factory that resolves BIRD tables via per-schema search_path.

    Engines are cached per schema so the harness only opens 5 connection pools
    for a 251-item run.
    """
    cache: dict[str, Engine] = {}

    def _factory(item: EvalItem) -> Engine:
        db_id = item.query["db_id"]
        schema = f"bird_{db_id}"
        engine = cache.get(schema)
        if engine is not None:
            return engine
        engine = make_engine(beacon_db_url)

        @sa_event.listens_for(engine, "connect")
        def _set_search_path(dbapi_connection: Any, _conn_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute(f'SET search_path TO "{schema}", public')
            cursor.close()

        cache[schema] = engine
        return engine

    return _factory
