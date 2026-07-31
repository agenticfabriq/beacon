"""SQLAlchemy engine and Session factory.

Engines are configured per-process. RLS session-var setter (see rls.py) runs
on every checked-out connection to bind app.current_user_id.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

if TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.engine import Engine
    from sqlalchemy.orm import Session


def make_engine(url: str | None = None) -> Engine:
    """Build a SQLAlchemy engine using ``url`` or the ``DATABASE_URL`` env var."""
    url = url or os.environ["DATABASE_URL"]
    return create_engine(url, future=True, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a ``sessionmaker`` bound to ``engine`` with expire-on-commit off."""
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield a session that commits on success and rolls back on error."""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
