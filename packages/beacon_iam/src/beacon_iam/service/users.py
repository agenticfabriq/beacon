from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_storage.repository.users import UserRepo

if TYPE_CHECKING:
    from beacon_storage.models.tenancy import User
    from sqlalchemy.orm import Session

    from beacon_iam.auth.oidc import OidcClaims


class UserService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = UserRepo(session)

    def upsert_from_oidc(self, claims: OidcClaims) -> User:
        """Find or create a user from OIDC claims, linking by subject then email."""
        existing = self.users.get_by_oidc_subject(claims.subject)
        if existing is not None:
            if existing.name != claims.name:
                existing.name = claims.name
                self.session.flush()
            return existing

        by_email = self.users.get_by_email(claims.email) if claims.email else None
        if by_email is not None and by_email.oidc_subject is None:
            by_email.oidc_subject = claims.subject
            by_email.name = claims.name
            self.session.flush()
            return by_email

        return self.users.create(
            email=claims.email or f"{claims.subject}@unknown.local",
            name=claims.name or claims.subject,
            oidc_subject=claims.subject,
        )
