"""Row-level security helpers.

Call set_current_user() once per request, immediately after the session is
acquired and before any queries. Postgres RLS policies read this session var.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import event, text

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.engine import Connection
    from sqlalchemy.orm import Session


# Where the acting user is remembered across transactions. The GUC itself is
# transaction-LOCAL by design -- a session-scoped one would leak the caller
# across requests on a pooled connection -- so a commit mid-request clears it
# and every policy falls to its `current_user_id() IS NULL` branch, which
# grants everything. Measured before this existed: a member who could see one
# team saw two after a commit, with the role still constrained. `bind_rls`
# below re-applies it when the next transaction begins.
SESSION_USER_KEY = "beacon_rls_user_id"


def set_current_user(session: Session, user_id: UUID) -> None:
    """Set app.current_user_id for this transaction, and for later ones.

    The GUC is transaction-local, so this also records the id on the session
    for ``bind_rls`` to re-apply. Without that, a route that commits partway
    -- ``delete_team``, ``remove_team_member``, ``issue_member_key`` all do --
    runs everything after the commit with no acting user, and the policies
    stop constraining anything.
    """
    session.info[SESSION_USER_KEY] = user_id
    session.execute(
        text("SELECT set_config('app.current_user_id', :uid, true)"),
        {"uid": str(user_id)},
    )


def bind_rls(session: Session) -> None:
    """Re-apply the acting user whenever a new transaction begins on ``session``.

    Registered once per request session, before anything queries. The listener
    writes through the raw ``connection`` rather than the session, because
    emitting session-level SQL from inside a transaction lifecycle hook
    re-enters the machinery that fired it.

    A session with no recorded user is left alone: the auth endpoints must run
    before a user is known, and the retention worker acts for nobody.
    """

    @event.listens_for(session, "after_begin")
    def _reapply(_session: Session, _transaction: object, connection: Connection) -> None:
        user_id = _session.info.get(SESSION_USER_KEY)
        if user_id is not None:
            connection.execute(
                text("SELECT set_config('app.current_user_id', :uid, true)"),
                {"uid": str(user_id)},
            )


def clear_current_user(session: Session) -> None:
    """Reset app.current_user_id for the current transaction."""
    session.execute(text("SELECT set_config('app.current_user_id', '', true)"))
