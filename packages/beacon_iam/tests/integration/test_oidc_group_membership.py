"""Team membership from the identity provider's groups.

Signing in worked and then nothing did: a new user could not read their own
team's runs until someone wrote a membership row by hand. Hit live -- the seed
user got `missing permission: project.view` until a team_admin grant was made
in SQL. Under a shared realm every new person becomes an operator ticket.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from beacon_iam.auth.oidc import OidcClaims
from beacon_iam.service.users import UserService
from beacon_storage.models.tenancy import Role, ScopeKind, Team

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture
def team_name(session: Session) -> str:
    name = f"grp-{uuid4().hex[:8]}"
    session.add(Team(name=name))
    session.flush()
    return name


def _claims(subject: str, *groups: str) -> OidcClaims:
    return OidcClaims(
        subject=subject, email=f"{subject}@example.com", name=subject, groups=groups
    )


def test_a_group_naming_a_team_grants_membership(session: Session, team_name: str) -> None:
    service = UserService(session)
    claims = _claims("newcomer", team_name)
    user = service.upsert_from_oidc(claims)

    granted = service.grant_memberships_from_groups(user, claims)

    assert granted == [team_name]
    memberships = service.memberships.list_for_user(user.id)
    assert [m.scope_kind for m in memberships] == [ScopeKind.TEAM]
    assert memberships[0].role == Role.TEAM_MEMBER


def test_a_group_naming_no_team_is_ignored(session: Session) -> None:
    """A realm carries groups for everything; only the ones we know mean anything."""
    service = UserService(session)
    claims = _claims("outsider", "some-unrelated-okta-group")
    user = service.upsert_from_oidc(claims)

    assert service.grant_memberships_from_groups(user, claims) == []


def test_signing_in_twice_does_not_duplicate_the_membership(
    session: Session, team_name: str
) -> None:
    service = UserService(session)
    claims = _claims("repeat", team_name)
    user = service.upsert_from_oidc(claims)

    service.grant_memberships_from_groups(user, claims)
    second = service.grant_memberships_from_groups(user, claims)

    assert second == []
    assert len(service.memberships.list_for_user(user.id)) == 1


def test_a_token_with_no_groups_claim_removes_nothing(
    session: Session, team_name: str
) -> None:
    """Silence is not an assertion of belonging to nothing.

    Treating it as removal would lock people out on the first provider that
    omits the claim, so revocation stays explicit.
    """
    service = UserService(session)
    with_groups = _claims("keeper", team_name)
    user = service.upsert_from_oidc(with_groups)
    service.grant_memberships_from_groups(user, with_groups)

    service.grant_memberships_from_groups(user, _claims("keeper"))

    assert len(service.memberships.list_for_user(user.id)) == 1


def test_admin_is_not_granted_by_a_group(session: Session, team_name: str) -> None:
    """Being in a group says you belong, not that you may manage."""
    service = UserService(session)
    claims = _claims("member", team_name)
    user = service.upsert_from_oidc(claims)

    service.grant_memberships_from_groups(user, claims)

    assert service.memberships.list_for_user(user.id)[0].role != Role.TEAM_ADMIN
