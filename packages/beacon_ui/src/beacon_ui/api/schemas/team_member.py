from __future__ import annotations

from typing import Literal
from uuid import UUID  # noqa: TC003

from beacon_storage.models.tenancy import Role, ScopeKind  # noqa: TC002
from pydantic import BaseModel, EmailStr

TeamRole = Literal["team_admin", "team_member"]


class TeamMemberIn(BaseModel):
    user_email: EmailStr
    role: TeamRole


class TeamMemberOut(BaseModel):
    user_id: UUID
    scope_kind: ScopeKind
    scope_id: UUID
    role: Role
