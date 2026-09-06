from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple
from uuid import UUID  # noqa: TC003 -- a NamedTuple field annotation is evaluated

from sqlalchemy import select, text

from beacon_storage.models.tenancy import User

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class InviteTarget(NamedTuple):
    """What an invite needs to know about an address, and nothing more.

    Deliberately not a ``User``. The caller cannot read that row -- see
    ``UserRepo.find_for_invite`` -- so a type promising one would be a lie
    that resolves to ``None``.
    """

    id: UUID
    is_active: bool


class UserRepo:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        email: str,
        name: str,
        oidc_subject: str | None = None,
        password_hash: str | None = None,
    ) -> User:
        """Persist a new user with the given identity fields and return it."""
        u = User(email=email, name=name, oidc_subject=oidc_subject, password_hash=password_hash)
        self.session.add(u)
        self.session.flush()
        return u

    def get(self, user_id: UUID) -> User | None:
        """Return the user with id ``user_id`` or None."""
        return self.session.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        """Return the user whose email equals ``email`` or None."""
        return self.session.scalar(select(User).where(User.email == email))

    def find_for_invite(self, email: str) -> InviteTarget | None:
        """The user for ``email``, found even when RLS would hide the row.

        ``get_by_email`` above is the ordinary read and is bounded by
        ``users_read``: self, people sharing a scope with the caller, and
        global admins. That is right for every other caller and wrong for
        exactly one -- adding a member has to know whether an address already
        has an account BEFORE creating one, and an invitee who is not yet a
        co-member is invisible. Measured: the lookup returned nothing, the
        route concluded "new", and the insert hit the unique constraint on
        ``users.email``, which no handler catches -- a 500.

        Answered by ``user_for_invite``, a ``SECURITY DEFINER`` function that
        resolves one address and returns nothing but its id and active flag.
        It refuses callers who administer nothing, so it does not let every
        authenticated user test arbitrary addresses for an account.

        Returns those two fields rather than a ``User``, and that is the point
        of the type: a first version returned ``User`` by re-reading the row
        with ``session.get``, which re-applies the very policy being worked
        around, so it handed back ``None`` for exactly the case it existed to
        answer. There is no ``User`` to give -- the caller may not read one.

        Named for its one use. A general "read any user" helper is what this
        exists to avoid.
        """
        row = self.session.execute(
            text("SELECT id, is_active FROM user_for_invite(:email)"), {"email": email}
        ).one_or_none()
        return None if row is None else InviteTarget(id=row.id, is_active=row.is_active)

    def get_by_oidc_subject(self, subject: str) -> User | None:
        """Return the user whose OIDC subject equals ``subject`` or None."""
        return self.session.scalar(select(User).where(User.oidc_subject == subject))
