from __future__ import annotations

from typing import Literal
from uuid import UUID  # noqa: TC003

from beacon_storage.models.tenancy import Role, ScopeKind  # noqa: TC002
from pydantic import BaseModel, ConfigDict, EmailStr

TeamRole = Literal["team_admin", "team_member"]


class TeamMemberIn(BaseModel):
    user_email: EmailStr
    role: TeamRole


class TeamMemberOut(BaseModel):
    user_id: UUID
    scope_kind: ScopeKind
    scope_id: UUID
    role: Role


class TeamMemberRowOut(BaseModel):
    """A member as a roster shows them: who, not just which id."""

    user_id: UUID
    email: str
    name: str
    role: Role


class TeamMemberListOut(BaseModel):
    members: list[TeamMemberRowOut]


class MemberKeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = "issued-by-admin"


class MemberKeyOut(BaseModel):
    """A key minted for a member by a team admin, shown exactly once."""

    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    label: str
    api_key: str

