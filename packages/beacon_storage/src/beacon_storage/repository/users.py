from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select

from beacon_storage.models.tenancy import User

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.orm import Session


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

    def get_by_oidc_subject(self, subject: str) -> User | None:
        """Return the user whose OIDC subject equals ``subject`` or None."""
        return self.session.scalar(select(User).where(User.oidc_subject == subject))
