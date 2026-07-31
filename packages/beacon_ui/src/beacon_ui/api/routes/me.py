from __future__ import annotations

from typing import Annotated

from beacon_storage.models.tenancy import User  # noqa: TC002
from beacon_storage.repository.memberships import MembershipRepo
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session  # noqa: TC002

from beacon_ui.api.deps import get_current_user, get_session
from beacon_ui.api.schemas.user import MembershipOut, MeOut, UserOut

router = APIRouter(prefix="/v1", tags=["me"])


@router.get(
    "/me",
    response_model=MeOut,
    description="Required roles: authenticated user.",
    openapi_extra={"x-required-roles": ["authenticated"]},
)
def get_me(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[Session, Depends(get_session)],
) -> MeOut:
    """Return the authenticated user's profile and memberships."""
    memberships = MembershipRepo(session).list_for_user(user.id)
    return MeOut(
        user=UserOut.model_validate(user),
        memberships=[MembershipOut.model_validate(membership) for membership in memberships],
    )
