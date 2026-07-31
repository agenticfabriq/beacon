"""Row-level security helpers.

Call set_current_user() once per request, immediately after the session is
acquired and before any queries. Postgres RLS policies read this session var.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import text

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


def set_current_user(session: Session, user_id: UUID) -> None:
    """Set app.current_user_id for the current transaction."""
    session.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id)},
    )


def clear_current_user(session: Session) -> None:
    """Reset app.current_user_id for the current transaction."""
    session.execute(text("SELECT set_config('app.current_user_id', '', true)"))
