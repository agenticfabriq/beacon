from __future__ import annotations

from typing import TYPE_CHECKING

from beacon_storage.models.tenancy import Role, ScopeKind
from beacon_storage.repository.memberships import MembershipRepo
from beacon_storage.repository.teams import TeamRepo
from beacon_storage.repository.users import UserRepo

if TYPE_CHECKING:
    from beacon_storage.models.tenancy import User
    from sqlalchemy.orm import Session

    from beacon_iam.auth.oidc import OidcClaims


class UserService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = UserRepo(session)
        self.teams = TeamRepo(session)
        self.memberships = MembershipRepo(session)

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

    def grant_memberships_from_groups(self, user: User, claims: OidcClaims) -> list[str]:
        """Grant team membership for each group naming a team, returning the names.

        Without this, every new user is an operator ticket: they sign in
        successfully and then cannot read their own team's runs until someone
        writes a membership row by hand.

        Grant-only, deliberately. A token that carries no groups claim is not
        asserting that the user belongs to nothing, and treating silence as
        removal would lock people out on the first identity provider that omits
        it. Revocation stays explicit, through the members endpoint.
        """
        granted: list[str] = []
        for group in claims.groups:
            team = self.teams.get_by_name(group)
            if team is None:
                continue
            if any(
                membership.scope_kind == ScopeKind.TEAM and membership.scope_id == team.id
                for membership in self.memberships.list_for_user(user.id)
            ):
                continue
            self.memberships.grant(
                user_id=user.id,
                scope_kind=ScopeKind.TEAM,
                scope_id=team.id,
                role=Role.TEAM_MEMBER,
                granted_by=user.id,
            )
            granted.append(team.name)
        return granted
